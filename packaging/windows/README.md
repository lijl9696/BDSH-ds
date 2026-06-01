# Windows 打包说明

在 Windows 电脑上打开命令提示符，进入项目根目录后运行：

```bat
build_windows.bat
```

脚本会自动创建 `.venv-win`、安装依赖并执行 PyInstaller。

打包完成后，把整个文件夹发给使用者：

```text
dist\团购报表工具
```

使用者只需要双击：

```text
dist\团购报表工具\团购报表工具.exe
```

不需要额外安装 Python 或依赖。

## 固定目录

程序会从 exe 同级目录读取：

```text
配置表\报表工具配置模板.xlsx
配置表\输出报表模板.xlsx
```

程序会输出到 exe 同级目录：

```text
outputs
data
```
