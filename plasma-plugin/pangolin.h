#ifndef PANGOLIN_H
#define PANGOLIN_H

#include "vpnuiplugin.h"

class Q_DECL_EXPORT PangolinUiPlugin : public VpnUiPlugin
{
    Q_OBJECT

public:
    explicit PangolinUiPlugin(QObject *parent = nullptr, const QVariantList &args = {});
    ~PangolinUiPlugin() override;

    SettingWidget *widget(const NetworkManager::VpnSetting::Ptr &setting, QWidget *parent) override;
    SettingWidget *askUser(const NetworkManager::VpnSetting::Ptr &setting, const QStringList &hints, QWidget *parent) override;
    QString suggestedFileName(const NetworkManager::ConnectionSettings::Ptr &connection) const override;
};

#endif // PANGOLIN_H
