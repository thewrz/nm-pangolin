#include "pangolinauth.h"
#include "ui_pangolinauth.h"

#include <QClipboard>
#include <QDir>
#include <QFileInfo>
#include <QGuiApplication>
#include <QRegularExpression>
#include <QStandardPaths>

static constexpr int k_pollIntervalMs = 2000;
static constexpr int k_defaultExpirySeconds = 600;

static QString pangolinBinaryPath()
{
    QString path = QStandardPaths::findExecutable(QStringLiteral("pangolin"));
    if (path.isEmpty()) {
        const QString localBin = QDir::homePath() + QStringLiteral("/.local/bin/pangolin");
        if (QFileInfo(localBin).isExecutable()) {
            path = localBin;
        }
    }
    return path;
}

PangolinAuthWidget::PangolinAuthWidget(const NetworkManager::VpnSetting::Ptr &setting, QWidget *parent)
    : SettingWidget(setting, parent)
    , m_ui(new Ui::PangolinAuthWidget)
    , m_setting(setting)
{
    m_ui->setupUi(this);

    m_ui->urlLabel->setTextInteractionFlags(Qt::TextSelectableByMouse | Qt::LinksAccessibleByMouse);
    m_ui->urlLabel->setOpenExternalLinks(true);
    m_ui->codeLabel->setTextInteractionFlags(Qt::TextSelectableByMouse);
    m_ui->getNewCodeBtn->setVisible(false);
    m_ui->progressBar->setVisible(false);
    m_ui->progressBar->setMinimum(0);
    m_ui->progressBar->setMaximum(0); // indeterminate
    m_ui->urlLabel->clear();
    m_ui->codeLabel->clear();
    m_ui->countdownLabel->clear();
    m_ui->copyCodeBtn->setVisible(false);
    m_ui->copyUrlBtn->setVisible(false);

    connect(m_ui->copyUrlBtn, &QPushButton::clicked, this, &PangolinAuthWidget::copyUrl);
    connect(m_ui->copyCodeBtn, &QPushButton::clicked, this, &PangolinAuthWidget::copyCode);
    connect(m_ui->getNewCodeBtn, &QPushButton::clicked, this, &PangolinAuthWidget::onGetNewCode);

    // Get server URL from the VPN connection settings
    if (setting && !setting->isNull()) {
        const NMStringMap data = setting->data();
        m_serverUrl = data.value(QStringLiteral("server-url"));
    }

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

        // auth status returns 0 when authenticated
        setAuthenticatedState();
    });

    process->start(binary, {QStringLiteral("auth"), QStringLiteral("status")});
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
    m_remainingSeconds = k_defaultExpirySeconds;

    // pangolin auth login outputs plain text to a TTY:
    //   "First copy your one-time code: XXXX-XXXX"
    //   "Press Enter to open https://... in your browser..."
    // We use 'script' to provide a pseudo-TTY since QProcess doesn't have one.

    QStringList scriptArgs;
    scriptArgs << QStringLiteral("-qc");

    // Build the pangolin command with server URL if available
    QString pangolinCmd = binary + QStringLiteral(" auth login");
    if (!m_serverUrl.isEmpty()) {
        pangolinCmd += QStringLiteral(" ") + m_serverUrl;
    }
    scriptArgs << pangolinCmd;
    scriptArgs << QStringLiteral("/dev/null");

    connect(m_authProcess, &QProcess::readyReadStandardOutput, this, [this]() {
        const QByteArray raw = m_authProcess->readAllStandardOutput();
        const QString output = QString::fromUtf8(raw);
        m_authBuffer += output;

        parseAuthOutput();
    });

    connect(m_authProcess, &QProcess::finished, this, &PangolinAuthWidget::onAuthProcessFinished);

    m_authProcess->start(QStringLiteral("script"), scriptArgs);
}

void PangolinAuthWidget::parseAuthOutput()
{
    // Look for: "one-time code: XXXX-XXXX"
    static const QRegularExpression codeRe(QStringLiteral("one-time code:\\s*([A-Z0-9]{4}-[A-Z0-9]{4})"));
    // Look for: "open https://... in your browser"
    static const QRegularExpression urlRe(QStringLiteral("(https?://[^\\s]+)\\s+in your browser"));

    const QRegularExpressionMatch codeMatch = codeRe.match(m_authBuffer);
    const QRegularExpressionMatch urlMatch = urlRe.match(m_authBuffer);

    if (codeMatch.hasMatch()) {
        m_deviceCode = codeMatch.captured(1);
    }
    if (urlMatch.hasMatch()) {
        m_verificationUrl = urlMatch.captured(1);
    }

    if (!m_deviceCode.isEmpty() && !m_verificationUrl.isEmpty()) {
        showDeviceCode();
    }
}

void PangolinAuthWidget::showDeviceCode()
{
    m_ui->statusLabel->setText(QStringLiteral("Authorize this device:"));

    m_ui->urlLabel->setText(QStringLiteral("<a href=\"%1\" style=\"color: #4fc3f7;\">%1</a>").arg(m_verificationUrl));

    m_ui->codeLabel->setText(
        QStringLiteral("<span style=\"font-size: 24pt; font-weight: bold; letter-spacing: 4px;\">%1</span>")
            .arg(m_deviceCode));

    m_ui->copyCodeBtn->setVisible(true);
    m_ui->copyCodeBtn->setEnabled(true);
    m_ui->copyUrlBtn->setVisible(true);
    m_ui->copyUrlBtn->setEnabled(true);
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

        setAuthenticatedState();
    });

    process->start(binary, {QStringLiteral("auth"), QStringLiteral("status")});
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
    m_authBuffer.clear();
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
    m_ui->copyUrlBtn->setVisible(false);
    m_ui->copyCodeBtn->setVisible(false);

    Q_EMIT settingChanged();
}

QVariantMap PangolinAuthWidget::setting() const
{
    NetworkManager::VpnSetting vpnSetting;
    vpnSetting.setServiceType(QStringLiteral("org.freedesktop.NetworkManager.pangolin"));
    return vpnSetting.toMap();
}

bool PangolinAuthWidget::isValid() const
{
    return m_authenticated;
}
