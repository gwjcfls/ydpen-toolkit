# 「文件互传」的 HTTP 服务只实现了 GET —— 网页上无法上传文件（附修复实现与两个 bug 提醒）

## 环境

| 项 | 值 |
|---|---|
| 机型 | 有道词典笔 YDPX6-2（X6 Pro，Rockchip RK3562 / aarch64 / PenOS 4.3.5） |
| 小程序 | file-manager v1.2.0（appid `8001771940015915`） |
| 相关插件 | `libjsapi_fileserver.so`（包里三套架构：arm / arm64 / arm64-cherry3566）、`libjsapi_shell.so` |

## 现象

进入「文件管理器 → 菜单 → 文件互传」，点「启动服务」后服务**确实起来了**（监听 8080），
电脑浏览器也能打开页面、能浏览目录、能下载文件，**但没有任何上传入口**；
手工 POST 也是失败的：

```
GET  /                       → 200（页面标题 "Index of /"，只有 Name / Size 两列表格）
POST /upload                 → 405 Method Not Allowed
```

## 根因

`libjsapi_fileserver.so` 的 HTTP 服务**只实现了 GET**：

- 内嵌 HTML 里没有任何 `<form>` / `<input type=file>`，页脚写着 `HaaS FileServer`；
- 插件字符串里 **`upload` 0 处、`multipart` 0 处、`form-data` 0 处**，只有一句 `get method failed %s`；
- 导出的方法只有 `start` / `stop` / `getStatus` / `getPort`（`usage: start(port, rootPath)`）。

也就是说：**app 的 UI 叫「文件互传」，但服务端只做了"传出去"这一半** —— 这不是配置问题，是功能未实现。

## 我做的修复（已在本机验证，可供参考/合并）

用同一套 jsapi ABI 重写了一个 `fileServer` 插件（保留原有 API 与导出名，`moduleName="fileServer"`，
导出 `default` / `FileServer` / `fileServer` 三个名字 —— app 用的是具名导入 `import { FileServer } from 'fileServer'`，
少一个就会 `SyntaxError: Could not find export 'FileServer' in module 'fileServer'` 而**整页黑屏**），补齐了：

| 接口 | 说明 |
|---|---|
| `GET /` | 新页面：**拖拽上传区 + 选择文件（多选）+ 新建文件夹 + 目录/文件列表** |
| `GET /?path=sub` | 浏览子目录 |
| `POST /upload?path=sub` | `multipart/form-data` **流式落盘**（不限大小）、中文名、重名自动加 `-1` |
| `GET /files/<rel>` | 下载，支持 `Range`（206，视频可拖动/断点续传） |
| `POST /mkdir?path=&name=` | 新建文件夹（已存在视为成功） |

实测（8 MB 中文名文件上传）：

```
POST /upload?path=uploadtest → 200 {"ok":true,"count":1,"files":["上传测试.bin"]}
笔上落盘 8388608 字节，md5 与本地一致 ✔
下载回来 md5 一致 ✔
Range: bytes=1000-1999 → 206 + Content-Range: bytes 1000-1999/8388608 ✔
```

## ⚠️ 两个我在开发中踩到、原作者可能也会踩的 bug（建议检查）

1. **multipart 解析"找到分隔符就返回"会丢数据**：如果找到 `\r\n--boundary` 时没有把**分隔符之前**已缓冲的数据
   先写盘，文件尾部就会少一截（我实测 8 MB 文件**少了 4019 字节**，md5 对不上）。
   修法：返回前先 `fwrite(buf+pos, 1, i, out)`。
2. **"缓冲满且找不到分隔符"时会空转死循环**：读取函数如果既不消费数据、也不读到新数据、又不退出，
   就会把 CPU 打满 —— 在词典笔上表现为**整机卡死、ADB 会话掉线**（我最初就踩了这个）。
   修法：保证每轮循环都有进展（消费安全部分 / 扩容 / 报错退出），并设缓冲上限。

## 其它小建议

- `miniapp_cli install` 在**同一 appid 已存在**时只更新 JS、**不会重新提取 `libs/*.so`**（实测）。
  所以升级包里带原生插件时，建议在文档里写明"先 `uninstall` 再 `install`"，或在 app 侧做一次自检提示。
- 32 位 `libs/arm/` 与 64 位 `libs/arm64*` 需要分别构建；本机走 `arm64-cherry3566`。

我这边有完整的插件源码（C，约 1300 行）与打好的 `file-manager-1.2.0-upload.amr`
（只替换了 `libs/arm64{,-cherry3566}/libjsapi_fileserver.so` 并同步了 `manifest.json` 的 cert，
其余文件逐字节未变），**如果愿意收，我可以整理成 PR**。
