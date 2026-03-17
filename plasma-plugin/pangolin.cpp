#include "pangolin.h"
#include "pangolinwidget.h"
#include "pangolinauth.h"

#include <KPluginFactory>

K_PLUGIN_CLASS_WITH_JSON(PangolinUiPlugin, "plasmanetworkmanagement_pangolinui.json")

PangolinUiPlugin::PangolinUiPlugin(QObject *parent, const QVariantList &args)
    : VpnUiPlugin(parent)
{
    Q_UNUSED(args)
}

PangolinUiPlugin::~PangolinUiPlugin() = default;

SettingWidget *PangolinUiPlugin::widget(const NetworkManager::VpnSetting::Ptr &setting, QWidget *parent)
{
    return new PangolinWidget(setting, parent);
}

SettingWidget *PangolinUiPlugin::askUser(const NetworkManager::VpnSetting::Ptr &setting,
                                         const QStringList &hints,
                                         QWidget *parent)
{
    Q_UNUSED(hints)
    return new PangolinAuthWidget(setting, parent);
}

QString PangolinUiPlugin::suggestedFileName(const NetworkManager::ConnectionSettings::Ptr &connection) const
{
    Q_UNUSED(connection)
    return QStringLiteral("pangolin-vpn");
}

#include "pangolin.moc"
