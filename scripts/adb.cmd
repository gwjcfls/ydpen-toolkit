@echo off
rem 把 adb 的配置目录放在工作区里，避免写 C:\Users\<你>\.android 权限报错
set "ANDROID_USER_HOME=%~dp0..\.android"
set "PATH=%~dp0platform-tools;%PATH%"
"%~dp0platform-tools\adb.exe" %*
