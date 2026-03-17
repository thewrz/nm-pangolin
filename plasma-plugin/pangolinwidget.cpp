#include "pangolinwidget.h"
#include "ui_pangolin.h"

#include <QDir>
#include <QFileInfo>
#include <QStandardPaths>

#include <NetworkManagerQt/Setting>

static const QString k_serverUrl = QStringLiteral("server-url");
static const QString k_org = QStringLiteral("org");
static const QString k_interfaceName = QStringLiteral("interface-name");
static const QString k_mtu = QStringLiteral("mtu");
static const QString k_olmId = QStringLiteral("olm-id");
static const QString k_olmSecret = QStringLiteral("olm-secret");

PangolinWidget::PangolinWidget(const NetworkManager::VpnSetting::Ptr &setting, QWidget *parent)
    : SettingWidget(setting, parent)
    , m_ui(new Ui::PangolinWidget)
    , m_setting(setting)
{
    m_ui->setupUi(this);

    m_ui->warningLabel->setVisible(false);

    checkPangolinBinary();

    if (setting && !setting->isNull()) {
        loadConfig(setting);
    }

    // watchChangedSetting connects all child widgets to settingChanged.
    // Must be called before the slotWidgetChanged connections below.
    watchChangedSetting();

    // Trigger validity re-evaluation when the server URL changes.
    // slotWidgetChanged() is protected, so we call it via lambda.
    connect(m_ui->serverUrl, &QLineEdit::textChanged, this, [this]() {
        slotWidgetChanged();
    });
}

PangolinWidget::~PangolinWidget()
{
    delete m_ui;
}

void PangolinWidget::loadConfig(const NetworkManager::Setting::Ptr &setting)
{
    const auto vpnSetting = setting.staticCast<NetworkManager::VpnSetting>();
    const NMStringMap data = vpnSetting->data();

    m_ui->serverUrl->setText(data.value(k_serverUrl));
    m_ui->org->setText(data.value(k_org));
    m_ui->interfaceName->setText(data.value(k_interfaceName));

    const int mtu = data.value(k_mtu, QStringLiteral("0")).toInt();
    m_ui->mtu->setValue(mtu);

    loadSecrets(setting);
}

void PangolinWidget::loadSecrets(const NetworkManager::Setting::Ptr &setting)
{
    const auto vpnSetting = setting.staticCast<NetworkManager::VpnSetting>();
    const NMStringMap secrets = vpnSetting->secrets();

    m_ui->olmId->setText(secrets.value(k_olmId));
    m_ui->olmSecret->setText(secrets.value(k_olmSecret));
}

QVariantMap PangolinWidget::setting() const
{
    NetworkManager::VpnSetting vpnSetting;
    vpnSetting.setServiceType(QStringLiteral("org.freedesktop.NetworkManager.pangolin"));

    NMStringMap data;
    const QString serverUrl = m_ui->serverUrl->text().trimmed();
    if (!serverUrl.isEmpty()) {
        data.insert(k_serverUrl, serverUrl);
    }

    const QString org = m_ui->org->text().trimmed();
    if (!org.isEmpty()) {
        data.insert(k_org, org);
    }

    const QString ifName = m_ui->interfaceName->text().trimmed();
    if (!ifName.isEmpty()) {
        data.insert(k_interfaceName, ifName);
    }

    const int mtu = m_ui->mtu->value();
    if (mtu > 0) {
        data.insert(k_mtu, QString::number(mtu));
    }

    vpnSetting.setData(data);

    NMStringMap secrets;
    const QString olmId = m_ui->olmId->text().trimmed();
    if (!olmId.isEmpty()) {
        secrets.insert(k_olmId, olmId);
    }

    const QString olmSecret = m_ui->olmSecret->text().trimmed();
    if (!olmSecret.isEmpty()) {
        secrets.insert(k_olmSecret, olmSecret);
    }

    vpnSetting.setSecrets(secrets);

    return vpnSetting.toMap();
}

bool PangolinWidget::isValid() const
{
    return !m_ui->serverUrl->text().trimmed().isEmpty();
}

void PangolinWidget::checkPangolinBinary()
{
    // Check PATH first
    QString path = QStandardPaths::findExecutable(QStringLiteral("pangolin"));

    // Also check ~/.local/bin/ (common for user-local installs)
    if (path.isEmpty()) {
        const QString localBin = QDir::homePath() + QStringLiteral("/.local/bin/pangolin");
        if (QFileInfo(localBin).isExecutable()) {
            path = localBin;
        }
    }

    if (path.isEmpty()) {
        m_ui->warningLabel->setText(
            QStringLiteral("<span style=\"color: red;\">Warning: pangolin binary not found in PATH or ~/.local/bin/</span>"));
        m_ui->warningLabel->setVisible(true);
    }
}
