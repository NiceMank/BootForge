#!/usr/bin/env python3
"""Safe Rufus-compatible Windows 10/11 (24H2/25H2) autounattend.xml builder."""

from __future__ import annotations

import re
from typing import Any, Dict, List
from xml.sax.saxutils import escape

RESERVED_WIN_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    "ADMINISTRATOR", "ADMINISTRATEUR", "GUEST", "INVITÉ", "DEFAULTACCOUNT", "WDAGUTILITYACCOUNT",
}

USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,20}$")
LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


def validate_windows_username(username: str) -> str:
    clean = (username or "").strip()
    if not clean:
        raise ValueError("Le nom d'utilisateur local ne peut pas être vide.")
    if not USERNAME_RE.match(clean):
        raise ValueError(
            "Le nom d'utilisateur local ne doit contenir que des lettres, chiffres, '.', '_' ou '-' (max 20)."
        )
    if clean.upper() in RESERVED_WIN_NAMES:
        raise ValueError(f"Nom d'utilisateur réservé par Windows : {clean}")
    return clean


def build_autounattend_xml(options: Dict[str, Any]) -> str:
    """Build a hardened, properly escaped autounattend.xml for Windows 10/11 (24H2/25H2)."""
    bypass_hw = bool(options.get("bypass_tpm_secureboot_ram", True))
    bypass_nro = bool(options.get("bypass_microsoft_account", True))
    disable_bitlocker = bool(options.get("disable_bitlocker", True))
    disable_telemetry = bool(options.get("disable_telemetry", True))
    disable_bloat = bool(options.get("disable_consumer_bloat", False))
    skip_product_key = bool(options.get("skip_product_key", True))
    create_local_account = bool(options.get("create_local_account", False))

    raw_locale = str(options.get("locale") or "fr-FR").strip()
    locale = raw_locale if LOCALE_RE.match(raw_locale) else "fr-FR"
    timezone = escape(str(options.get("timezone") or "Romance Standard Time").strip()[:64])
    arch = "arm64" if str(options.get("architecture") or "").lower() == "arm64" else "amd64"

    # Windows PE Pass: Hardware requirement bypasses + skip product key
    pe_commands: List[str] = []
    if bypass_hw:
        pe_commands.extend([
            r"reg add HKLM\SYSTEM\Setup\LabConfig /v BypassTPMCheck /t REG_DWORD /d 1 /f",
            r"reg add HKLM\SYSTEM\Setup\LabConfig /v BypassSecureBootCheck /t REG_DWORD /d 1 /f",
            r"reg add HKLM\SYSTEM\Setup\LabConfig /v BypassRAMCheck /t REG_DWORD /d 1 /f",
            r"reg add HKLM\SYSTEM\Setup\LabConfig /v BypassCPUCheck /t REG_DWORD /d 1 /f",
            r"reg add HKLM\SYSTEM\Setup\LabConfig /v BypassStorageCheck /t REG_DWORD /d 1 /f",
        ])

    pe_run_sync = ""
    if pe_commands:
        items = "\n".join(
            f"""                <RunSynchronousCommand wcm:action="add">
                    <Order>{idx}</Order>
                    <Path>{escape(cmd)}</Path>
                </RunSynchronousCommand>"""
            for idx, cmd in enumerate(pe_commands, start=1)
        )
        pe_run_sync = f"""            <RunSynchronous>
{items}
            </RunSynchronous>"""

    user_data_block = ""
    if skip_product_key:
        user_data_block = """            <UserData>
                <ProductKey>
                    <Key></Key>
                    <WillShowUI>Never</WillShowUI>
                </ProductKey>
                <AcceptEula>true</AcceptEula>
            </UserData>"""

    windows_pe_section = ""
    if pe_run_sync or user_data_block:
        windows_pe_section = f"""    <settings pass="windowsPE">
        <component name="Microsoft-Windows-Setup" processorArchitecture="{arch}" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
{pe_run_sync}
{user_data_block}
        </component>
    </settings>"""

    # Specialize Pass: BypassNRO, BitLocker off, Telemetry & Consumer Bloat
    spec_commands: List[str] = []
    if bypass_nro:
        spec_commands.append(
            r"reg add HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\OOBE /v BypassNRO /t REG_DWORD /d 1 /f"
        )
    if disable_bitlocker:
        spec_commands.append(
            r"reg add HKLM\SYSTEM\CurrentControlSet\Control\BitLocker /v PreventDeviceEncryption /t REG_DWORD /d 1 /f"
        )
    if disable_telemetry:
        spec_commands.extend([
            r"reg add HKLM\SOFTWARE\Policies\Microsoft\Windows\DataCollection /v AllowTelemetry /t REG_DWORD /d 0 /f",
            r"reg add HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\DataCollection /v AllowTelemetry /t REG_DWORD /d 0 /f",
        ])
    if disable_bloat:
        spec_commands.extend([
            r"reg add HKLM\SOFTWARE\Policies\Microsoft\Windows\CloudContent /v DisableWindowsConsumerFeatures /t REG_DWORD /d 1 /f",
            r"reg add HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsCopilot /v TurnOffWindowsCopilot /t REG_DWORD /d 1 /f",
        ])

    specialize_section = ""
    if spec_commands:
        spec_items = "\n".join(
            f"""                <RunSynchronousCommand wcm:action="add">
                    <Order>{idx}</Order>
                    <Path>{escape(cmd)}</Path>
                </RunSynchronousCommand>"""
            for idx, cmd in enumerate(spec_commands, start=1)
        )
        specialize_section = f"""    <settings pass="specialize">
        <component name="Microsoft-Windows-Deployment" processorArchitecture="{arch}" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
            <RunSynchronous>
{spec_items}
            </RunSynchronous>
        </component>
        <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="{arch}" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
            <TimeZone>{timezone}</TimeZone>
        </component>
    </settings>"""

    # OOBE Pass: Privacy + optional deterministic Local Account creation (24H2/25H2 bulletproof)
    oobe_flags = []
    if bypass_nro or create_local_account:
        oobe_flags.append("                <HideOnlineAccountScreens>true</HideOnlineAccountScreens>")
    if disable_telemetry:
        oobe_flags.extend([
            "                <ProtectYourPC>3</ProtectYourPC>",
            "                <HideEULAPage>true</HideEULAPage>",
            "                <HideWirelessSetupInOOBE>false</HideWirelessSetupInOOBE>",
        ])

    oobe_block = ""
    if oobe_flags:
        joined_flags = "\n".join(oobe_flags)
        oobe_block = f"""            <OOBE>
{joined_flags}
            </OOBE>"""

    user_accounts_block = ""
    if create_local_account:
        username = validate_windows_username(str(options.get("local_username") or "Utilisateur"))
        password = str(options.get("local_password") or "")
        safe_user = escape(username)
        safe_pass = escape(password)
        user_accounts_block = f"""            <UserAccounts>
                <LocalAccounts>
                    <LocalAccount wcm:action="add">
                        <Password>
                            <Value>{safe_pass}</Value>
                            <PlainText>true</PlainText>
                        </Password>
                        <Name>{safe_user}</Name>
                        <DisplayName>{safe_user}</DisplayName>
                        <Group>Administrators</Group>
                    </LocalAccount>
                </LocalAccounts>
            </UserAccounts>"""

    oobe_section = ""
    if oobe_block or user_accounts_block:
        oobe_section = f"""    <settings pass="oobeSystem">
        <component name="Microsoft-Windows-International-Core" processorArchitecture="{arch}" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
            <InputLocale>{locale}</InputLocale>
            <SystemLocale>{locale}</SystemLocale>
            <UILanguage>{locale}</UILanguage>
            <UserLocale>{locale}</UserLocale>
        </component>
        <component name="Microsoft-Windows-Shell-Setup" processorArchitecture="{arch}" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS" xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
{oobe_block}
{user_accounts_block}
        </component>
    </settings>"""

    parts = [p for p in (windows_pe_section, specialize_section, oobe_section) if p]
    body = "\n".join(parts)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend">
{body}
</unattend>
"""
