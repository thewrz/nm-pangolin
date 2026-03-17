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

# plasma-nm does not ship development headers or cmake configs.
# We vendor the required base class headers (LGPL) in libs/editor/
# and link against the installed libplasmanm_editor.so at runtime.
find_library(PLASMANM_EDITOR_LIB plasmanm_editor
    PATHS /usr/lib /usr/lib64 /usr/lib/x86_64-linux-gnu
    REQUIRED
)

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

target_include_directories(plasmanetworkmanagement_pangolinui PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/libs/editor
)

target_link_libraries(plasmanetworkmanagement_pangolinui
    Qt6::Core
    Qt6::DBus
    Qt6::Widgets
    KF6::CoreAddons
    KF6::I18n
    KF6::WidgetsAddons
    KF6::NetworkManagerQt
    ${PLASMANM_EDITOR_LIB}
)

# Find the actual VPN plugin directory by locating where plasma-nm installed
# its plugins. qtpaths and KDE_INSTALL_PLUGINDIR can both return stale paths.
find_file(_existing_vpn_plugin
    NAMES plasmanetworkmanagement_openvpnui.so
          plasmanetworkmanagement_vpncui.so
          plasmanetworkmanagement_pptpui.so
    PATHS
        /usr/lib/qt6/plugins/plasma/network/vpn
        /usr/lib64/qt6/plugins/plasma/network/vpn
        /usr/lib/qt/plugins/plasma/network/vpn
        ${KDE_INSTALL_PLUGINDIR}/plasma/network/vpn
    NO_DEFAULT_PATH
)

if(_existing_vpn_plugin)
    get_filename_component(_vpn_plugin_dir "${_existing_vpn_plugin}" DIRECTORY)
else()
    set(_vpn_plugin_dir "${KDE_INSTALL_PLUGINDIR}/plasma/network/vpn")
endif()

message(STATUS "VPN plugin install dir: ${_vpn_plugin_dir}")

install(TARGETS plasmanetworkmanagement_pangolinui
    DESTINATION ${_vpn_plugin_dir}
)

install(FILES plasmanetworkmanagement_pangolinui.json
    DESTINATION ${_vpn_plugin_dir}
)
