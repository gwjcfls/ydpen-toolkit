<#
hotspot.ps1 —— 尝试用 WinRT 接口一键开启 Windows 移动热点（共享当前 WLAN）

如果失败（不同系统/权限差异），请手动开：
    设置 → 网络和 Internet → 移动热点 → 打开（共享对象选 WLAN）
然后运行 tools\hosts_tool.ps1 -Add 192.168.137.1
#>
param([switch]$Off)

$ErrorActionPreference = 'Stop'

try {
    [void][Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]
    [void][Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]

    $profile = [Windows.Networking.Connectivity.NetworkInformation]::GetInternetConnectionProfile()
    if (-not $profile) { throw '当前没有可共享的互联网连接（先连上 Wi-Fi）' }

    $mgr = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::CreateFromConnectionProfile($profile)
    if ($Off) {
        $null = $mgr.StopTetheringAsync().GetResults()
        Write-Host '[-] 移动热点已关闭'
    } else {
        $r = $mgr.StartTetheringAsync().GetResults()
        Write-Host "[+] 移动热点状态: $($r.Status)"
    }
} catch {
    Write-Host "[!] WinRT 方式开热点失败: $($_.Exception.Message)"
    Write-Host '    请手动：设置 → 网络和 Internet → 移动热点 → 打开'
    exit 1
}

ipconfig | Select-String -Pattern 'IPv4|无线|WLAN' | Select-Object -First 12
