<#
hosts_tool.ps1 —— 把 iotapi.abupdate.com 劫持到本机（有道词典笔 OTA 欺骗用）

用法（普通权限运行即可，脚本会自己弹 UAC 提权）：
    powershell -ExecutionPolicy Bypass -File tools\hosts_tool.ps1 -List
    powershell -ExecutionPolicy Bypass -File tools\hosts_tool.ps1 -Add 192.168.137.1
    powershell -ExecutionPolicy Bypass -File tools\hosts_tool.ps1 -Remove

说明：Windows 移动热点(ICS) 默认网关是 192.168.137.1，一般就填这个。
      如果词典笔是从路由器 Wi-Fi 上网、电脑也在同一网段，就填电脑的局域网 IP。
#>
param(
    [switch]$Add,
    [string]$Ip,
    [switch]$Remove,
    [switch]$List,
    [string]$Domain = 'iotapi.abupdate.com'
)

$ErrorActionPreference = 'Stop'
$hostsPath = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
$marker = '# ydpen-adb hijack'

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin) -and -not $List) {
    Write-Host '[*] 需要管理员权限，正在弹 UAC …'
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    if ($Add) { $argList += @('-Add', $Ip) }
    if ($Remove) { $argList += '-Remove' }
    if ($List) { $argList += '-List' }
    if ($Domain) { $argList += @('-Domain', $Domain) }
    $p = Start-Process -FilePath 'powershell' -ArgumentList $argList -Verb RunAs -Wait -PassThru
    exit $p.ExitCode
}

function Show-Hosts {
    Write-Host "[*] 当前 $Domain 相关行："
    Select-String -Path $hostsPath -Pattern ([regex]::Escape($Domain)) -SimpleMatch | ForEach-Object { '    ' + $_.Line }
}

if ($List) { Show-Hosts; exit 0 }

if ($Add) {
    if (-not $Ip) { Write-Host '[!] -Add 需要给出 -Ip'; exit 1 }
    $lines = Get-Content -Path $hostsPath -ErrorAction SilentlyContinue
    if ($null -eq $lines) { $lines = @() }
    $kept = $lines | Where-Object { $_ -notmatch [regex]::Escape($Domain) }
    $new = @($kept) + @("$Ip`t$Domain`t$marker")
    Set-Content -Path $hostsPath -Value $new -Encoding ASCII
    Write-Host "[+] 已写入: $Ip`t$Domain"
    ipconfig /flushdns | Out-Null
    Write-Host '[+] DNS 缓存已刷新'
    Show-Hosts
}

if ($Remove) {
    $lines = Get-Content -Path $hostsPath -ErrorAction SilentlyContinue
    $kept = $lines | Where-Object { $_ -notmatch [regex]::Escape($Domain) }
    Set-Content -Path $hostsPath -Value $kept -Encoding ASCII
    ipconfig /flushdns | Out-Null
    Write-Host '[-] 已移除劫持记录，DNS 已刷新'
    Show-Hosts
}
