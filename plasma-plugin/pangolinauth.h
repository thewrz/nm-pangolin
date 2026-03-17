#ifndef PANGOLINAUTH_H
#define PANGOLINAUTH_H

#include "settingwidget.h"

#include <NetworkManagerQt/VpnSetting>

#include <QProcess>
#include <QTimer>

namespace Ui {
class PangolinAuthWidget;
}

class PangolinAuthWidget : public SettingWidget
{
    Q_OBJECT

public:
    explicit PangolinAuthWidget(const NetworkManager::VpnSetting::Ptr &setting, QWidget *parent = nullptr);
    ~PangolinAuthWidget() override;

    QVariantMap setting() const override;
    bool isValid() const override;

private Q_SLOTS:
    void checkAuthStatus();
    void startDeviceCodeFlow();
    void onAuthProcessFinished(int exitCode, QProcess::ExitStatus status);
    void pollAuthStatus();
    void updateCountdown();
    void onGetNewCode();
    void copyUrl();
    void copyCode();

private:
    void stopTimers();
    void stopAuthProcess();
    void setAuthenticatedState();
    void parseAuthOutput();
    void showDeviceCode();

    Ui::PangolinAuthWidget *m_ui = nullptr;
    NetworkManager::VpnSetting::Ptr m_setting;
    QProcess *m_authProcess = nullptr;
    QTimer *m_pollTimer = nullptr;
    QTimer *m_countdownTimer = nullptr;
    int m_remainingSeconds = 0;
    QString m_deviceCode;
    QString m_verificationUrl;
    QString m_serverUrl;
    QString m_authBuffer;
    bool m_authenticated = false;
    bool m_codeShown = false;
};

#endif // PANGOLINAUTH_H
