cmake_minimum_required(VERSION 3.22)
project(plasma-networkmanagement-pangolin)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(ECM REQUIRED NO_MODULE)
set(CMAKE_MODULE_PATH ${ECM_MODULE_PATH})

include(KDEInstallDirs)
include(KDECMakeSettings)
include(KDECompilerSettings NO_POLICY_SCOPE)

find_package(Qt6 REQUIRED COMPONENTS Core DBus Widgets)
find_package(KF6 REQUIRED COMPONENTS CoreAddons I18n WidgetsAddons)
find_package(KF6NetworkManagerQt REQUIRED)

# NOTE: The plasma-nm VPN UI plugin headers may come from different sources
# depending on the distribution. The find_package name and target may need
# adjustment. Check your plasma-nm development package for the actual
# CMake config file name (e.g., PlasmaNetworkManagerQt, plasma-nm, etc.).
find_package(PlasmaNetworkManagerQt REQUIRED)

set(pangolin_SRCS
    pangolin.cpp
    pangolinwidget.cpp
    pangolinauth.cpp
)

set(pangolin_UIS
    pangolin.ui
    pangolinauth.ui
)

qt6_wrap_ui(pangolin_UI_SRCS ${pangolin_UIS})

add_library(plasmanetworkmanagement_pangolinui MODULE
    ${pangolin_SRCS}
    ${pangolin_UI_SRCS}
)

target_link_libraries(plasmanetworkmanagement_pangolinui
    Qt6::Core
    Qt6::DBus
    Qt6::Widgets
    KF6::CoreAddons
    KF6::I18n
    KF6::WidgetsAddons
    KF6::NetworkManagerQt
    PlasmaNetworkManagerQt::plasmanm_internal
)

install(TARGETS plasmanetworkmanagement_pangolinui
    DESTINATION ${KDE_INSTALL_PLUGINDIR}/plasma/network/vpn
)

install(FILES plasmanetworkmanagement_pangolinui.json
    DESTINATION ${KDE_INSTALL_PLUGINDIR}/plasma/network/vpn
)
