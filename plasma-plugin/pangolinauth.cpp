#include "pangolinauth.h"
#include "ui_pangolinauth.h"

#include <QClipboard>
#include <QGuiApplication>
#include <QJsonDocument>
#include <QJsonObject>
#include <QStandardPaths>

static constexpr int k_pollIntervalMs = 2000;
static constexpr int k_defaultExpirySeconds = 600;

static QString pangolinBinaryPath()
{
    return QStandardPaths::findExecutable(QStringLiteral("pangolin"));
}

PangolinAuthWidget::PangolinAuthWidget(const NetworkManager::VpnSetting::Ptr &setting, QWidget *parent)
    : SettingWidget(setting, parent)
    , m_ui(new Ui::PangolinAuthWidget)
    , m_setting(setting)
{
    m_ui->setupUi(this);

    m_ui->urlLabel->setTextInteractionFlags(Qt::TextSelectableByMouse);
    m_ui->codeLabel->setTextInteractionFlags(Qt::TextSelectableByMouse);
    m_ui->getNewCodeBtn->setVisible(false);
    m_ui->progressBar->setVisible(false);
    m_ui->progressBar->setMinimum(0);
    m_ui->progressBar->setMaximum(0); // indeterminate
    m_ui->urlLabel->clear();
    m_ui->codeLabel->clear();
    m_ui->countdownLabel->clear();
    m_ui->copyUrlBtn->setEnabled(false);
    m_ui->copyCodeBtn->setEnabled(false);

    connect(m_ui->copyUrlBtn, &QPushButton::clicked, this, &PangolinAuthWidget::copyUrl);
    connect(m_ui->copyCodeBtn, &QPushButton::clicked, this, &PangolinAuthWidget::copyCode);
    connect(m_ui->getNewCodeBtn, &QPushButton::clicked, this, &PangolinAuthWidget::onGetNewCode);

    checkAuthStatus();
}

PangolinAuthWidget::~PangolinAuthWidget()
{
    stopAuthProcess();
    stopTimers();
    delete m_ui;
}

void PangolinAuthWidget::checkAuthStatus()
{
    m_ui->statusLabel->setText(QStringLiteral("Checking authentication..."));

    const QString binary = pangolinBinaryPath();
    if (binary.isEmpty()) {
        m_ui->statusLabel->setText(QStringLiteral("Error: pangolin binary not found"));
        return;
    }

    auto *process = new QProcess(this);
    connect(process, &QProcess::finished, this, [this, process](int exitCode, QProcess::ExitStatus status) {
        process->deleteLater();

        if (status != QProcess::NormalExit || exitCode != 0) {
            startDeviceCodeFlow();
            return;
        }

        const QByteArray output = process->readAllStandardOutput();
        const QJsonDocument doc = QJsonDocument::fromJson(output);

        if (doc.isObject() && doc.object().value(QStringLiteral("authenticated")).toBool()) {
            setAuthenticatedState();
        } else {
            startDeviceCodeFlow();
        }
    });

    process->start(binary, {QStringLiteral("auth"), QStringLiteral("status"), QStringLiteral("--json")});
}

void PangolinAuthWidget::startDeviceCodeFlow()
{
    m_ui->statusLabel->setText(QStringLiteral("Starting authentication..."));
    m_ui->getNewCodeBtn->setVisible(false);

    const QString binary = pangolinBinaryPath();
    if (binary.isEmpty()) {
        m_ui->statusLabel->setText(QStringLiteral("Error: pangolin binary not found"));
        return;
    }

    stopAuthProcess();

    m_authProcess = new QProcess(this);
    connect(m_authProcess, &QProcess::finished, this, &PangolinAuthWidget::onAuthProcessFinished);

    connect(m_authProcess, &QProcess::readyReadStandardOutput, this, [this]() {
        const QByteArray output = m_authProcess->readAllStandardOutput();
        const QJsonDocument doc = QJsonDocument::fromJson(output);

        if (!doc.isObject()) {
            return;
        }

        const QJsonObject obj = doc.object();
        m_verificationUrl = obj.value(QStringLiteral("verification_url")).toString();
        m_deviceCode = obj.value(QStringLiteral("user_code")).toString();
        m_remainingSeconds = obj.value(QStringLiteral("expires_in")).toInt(k_defaultExpirySeconds);

        if (m_verificationUrl.isEmpty() || m_deviceCode.isEmpty()) {
            return;
        }

        m_ui->statusLabel->setText(QStringLiteral("Authorize this device:"));
        m_ui->urlLabel->setText(m_verificationUrl);
        m_ui->codeLabel->setText(QStringLiteral("<span style=\"font-size: 18pt; font-weight: bold;\">%1</span>").arg(m_deviceCode));
        m_ui->copyUrlBtn->setEnabled(true);
        m_ui->copyCodeBtn->setEnabled(true);
        m_ui->progressBar->setVisible(true);

        updateCountdown();

        if (!m_pollTimer) {
            m_pollTimer = new QTimer(this);
            connect(m_pollTimer, &QTimer::timeout, this, &PangolinAuthWidget::pollAuthStatus);
        }
        m_pollTimer->start(k_pollIntervalMs);

        if (!m_countdownTimer) {
            m_countdownTimer = new QTimer(this);
            connect(m_countdownTimer, &QTimer::timeout, this, &PangolinAuthWidget::updateCountdown);
        }
        m_countdownTimer->start(1000);
    });

    m_authProcess->start(binary, {QStringLiteral("auth"), QStringLiteral("login"), QStringLiteral("--json")});
}

void PangolinAuthWidget::onAuthProcessFinished(int exitCode, QProcess::ExitStatus status)
{
    Q_UNUSED(exitCode)
    Q_UNUSED(status)
    m_authProcess = nullptr;
}

void PangolinAuthWidget::pollAuthStatus()
{
    const QString binary = pangolinBinaryPath();
    if (binary.isEmpty()) {
        return;
    }

    auto *process = new QProcess(this);
    connect(process, &QProcess::finished, this, [this, process](int exitCode, QProcess::ExitStatus status) {
        process->deleteLater();

        if (status != QProcess::NormalExit || exitCode != 0) {
            return;
        }

        const QByteArray output = process->readAllStandardOutput();
        const QJsonDocument doc = QJsonDocument::fromJson(output);

        if (doc.isObject() && doc.object().value(QStringLiteral("authenticated")).toBool()) {
            setAuthenticatedState();
        }
    });

    process->start(binary, {QStringLiteral("auth"), QStringLiteral("status"), QStringLiteral("--json")});
}

void PangolinAuthWidget::updateCountdown()
{
    m_remainingSeconds--;

    if (m_remainingSeconds <= 0) {
        stopTimers();
        m_ui->countdownLabel->setText(QStringLiteral("Code expired"));
        m_ui->progressBar->setVisible(false);
        m_ui->getNewCodeBtn->setVisible(true);
        m_ui->copyUrlBtn->setEnabled(false);
        m_ui->copyCodeBtn->setEnabled(false);
        return;
    }

    const int minutes = m_remainingSeconds / 60;
    const int seconds = m_remainingSeconds % 60;
    m_ui->countdownLabel->setText(QStringLiteral("Code expires in: %1:%2")
                                      .arg(minutes)
                                      .arg(seconds, 2, 10, QLatin1Char('0')));
}

void PangolinAuthWidget::onGetNewCode()
{
    stopAuthProcess();
    stopTimers();
    m_deviceCode.clear();
    m_verificationUrl.clear();
    m_ui->urlLabel->clear();
    m_ui->codeLabel->clear();
    m_ui->countdownLabel->clear();
    startDeviceCodeFlow();
}

void PangolinAuthWidget::copyUrl()
{
    if (!m_verificationUrl.isEmpty()) {
        QGuiApplication::clipboard()->setText(m_verificationUrl);
    }
}

void PangolinAuthWidget::copyCode()
{
    if (!m_deviceCode.isEmpty()) {
        QGuiApplication::clipboard()->setText(m_deviceCode);
    }
}

void PangolinAuthWidget::stopTimers()
{
    if (m_pollTimer) {
        m_pollTimer->stop();
    }
    if (m_countdownTimer) {
        m_countdownTimer->stop();
    }
}

void PangolinAuthWidget::stopAuthProcess()
{
    if (m_authProcess) {
        m_authProcess->disconnect(this);
        m_authProcess->kill();
        m_authProcess->waitForFinished(1000);
        m_authProcess->deleteLater();
        m_authProcess = nullptr;
    }
}

void PangolinAuthWidget::setAuthenticatedState()
{
    m_authenticated = true;
    stopTimers();
    stopAuthProcess();

    m_ui->statusLabel->setText(QStringLiteral("Authenticated"));
    m_ui->urlLabel->clear();
    m_ui->codeLabel->clear();
    m_ui->countdownLabel->clear();
    m_ui->progressBar->setVisible(false);
    m_ui->getNewCodeBtn->setVisible(false);
    m_ui->copyUrlBtn->setEnabled(false);
    m_ui->copyCodeBtn->setEnabled(false);

    Q_EMIT settingChanged();
}

QVariantMap PangolinAuthWidget::setting() const
{
    NetworkManager::VpnSetting vpnSetting;
    vpnSetting.setServiceType(QStringLiteral("org.freedesktop.NetworkManager.pangolin"));

    // Auth is handled by pangolin CLI, no secrets to pass back to NM
    // from the device code flow. The pangolin binary stores its own tokens.
    return vpnSetting.toMap();
}

bool PangolinAuthWidget::isValid() const
{
    return m_authenticated;
}
