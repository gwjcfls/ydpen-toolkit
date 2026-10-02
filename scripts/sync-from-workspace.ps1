<#
sync-from-workspace.ps1 —— 把工作区的最新工具/文档/插件同步进本 skill

用法:
    powershell -ExecutionPolicy Bypass -File "<skill目录>\scripts\sync-from-workspace.ps1"
    # 工作区换位置时指定:
    powershell ... -File "sync-from-workspace.ps1" -Workspace "D:\path\to\ydpen-adb"

本 skill 目录是"快照 + 自包含副本"，工作区才是日常开发的地方。
工作区有新进展（新脚本、新插件、改好的 amr 说明）后跑一次这个脚本即可。
#>
param(
    [string]$Workspace = 'C:\Users\Sever\Documents\deepseek-harness\default-workspace\ydpen-adb'
)

$ErrorActionPreference = 'Stop'
$skill = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

if (-not (Test-Path $Workspace)) {
    Write-Host "[!] 找不到工作区: $Workspace"
    exit 1
}
Write-Host "[*] 工作区: $Workspace"
Write-Host "[*] Skill : $skill"

foreach ($d in 'scripts', 'assets\plugins', 'assets\quickjs', 'reference\workspace-docs') {
    New-Item -ItemType Directory -Force -Path (Join-Path $skill $d) | Out-Null
}

$map = @(
    @{ from = 'tools';        to = 'scripts';        filter = 'ydpen.py','adb.cmd','_console.py','install_fs_plugin.py','fileserver_plugin.py','pack_upload_amr.py','lan_scan_adb.py','patch_firmware.py','ota_meta.py','fake_ota_server.py','file_server.py','fast_download.py','hosts_tool.ps1','hotspot.ps1','pen_recon.py','find_amr_packages.py','analyze_miniapp.py','parse_heapsnapshot.py','map_tokens.py','pstore_catalog.py','analyze_heapsnapshot.py','crack_adb_md5.py','crack_adb_md5_mp.py','hc_campaign.py','try_bypass.py' },
    @{ from = 'tools\build';  to = 'scripts';        filter = 'dltest.c','test_upload.py','tap.c','tap','fssrv','dltest','build.cmd' },
    @{ from = 'tools\build';  to = 'assets\plugins'; filter = 'fs_plugin.c','fileserver_plugin.c','libjsapi_fs_1000000001.so','libjsapi_fileserver_9000000001.so' },
    @{ from = 'tools\build\quickjs'; to = 'assets\quickjs'; filter = 'quickjs.h','quickjs-atom.h','quickjs-libc.h','quickjs-opcode.h','cutils.h','list.h','libunicode.h','VERSION' },
    @{ from = '.';            to = 'reference\workspace-docs'; filter = 'README.md','MINIAPPS.md','战果与使用说明.md','文件互传上传修复说明.md' },
    @{ from = 'miniapps';     to = 'reference\workspace-docs'; filter = '全新设备安装指南.md' }
)

$n = 0
foreach ($m in $map) {
    $src = Join-Path $Workspace $m.from
    $dst = Join-Path $skill $m.to
    foreach ($f in $m.filter) {
        $p = Join-Path $src $f
        if (Test-Path $p) {
            Copy-Item $p $dst -Force
            $n++
        }
    }
}
Write-Host "[+] 已同步 $n 个文件。"

# 完整性自检：SKILL.md 存在且首行是 frontmatter
$skill_md = Join-Path $skill 'SKILL.md'
if (-not (Test-Path $skill_md)) { Write-Host "[!] 缺少 SKILL.md"; exit 2 }
$first = (Get-Content $skill_md -TotalCount 1)
if ($first -ne '---') { Write-Host "[!] SKILL.md 首行不是 ---（frontmatter 丢失）"; exit 3 }

Write-Host "[*] 当前 skill 文件数: $((Get-ChildItem $skill -Recurse -File).Count)"
Write-Host "[+] 完成。DSH 会实时重新扫描 skill 目录，无需重启会话。"
