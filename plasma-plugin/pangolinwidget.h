#ifndef PANGOLINWIDGET_H
#define PANGOLINWIDGET_H

#include "settingwidget.h"

#include <NetworkManagerQt/VpnSetting>

namespace Ui {
class PangolinWidget;
}

class PangolinWidget : public SettingWidget
{
    Q_OBJECT

public:
    explicit PangolinWidget(const NetworkManager::VpnSetting::Ptr &setting, QWidget *parent = nullptr);
    ~PangolinWidget() override;

    void loadConfig(const NetworkManager::Setting::Ptr &setting) override;
    void loadSecrets(const NetworkManager::Setting::Ptr &setting) override;
    QVariantMap setting() const override;
    bool isValid() const override;

private:
    void checkPangolinBinary();

    Ui::PangolinWidget *m_ui = nullptr;
    NetworkManager::VpnSetting::Ptr m_setting;
};

#endif // PANGOLINWIDGET_H
