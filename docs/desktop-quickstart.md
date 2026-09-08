# 直播经营复盘桌面版使用说明

桌面版是在现有M2结算工作台和M3经营分析外增加的本机窗口，不改变账本、核算公式、关账版本或只读MCP。当前阶段是macOS arm64开发验证版。

2026-09-08已同步到0.1.0 Build 5：切换步骤、筛选和费用维护保留场次或导入范围；经营利润可预览所选场次完整数据，其他已明确归属场次缺成本不再阻断局部结果。共享费用与正式关账仍按整月明确标注。源码和桌面隔离环境均193项回归通过；应用包已完成临时签名校验和独立合成账本启动验证，包内不含账本、业务Excel或用户配置。原路径应用已更新并启动，保留原应用目录与Finder替身目标。

## 开发环境启动

在项目根目录创建隔离环境并安装桌面依赖：

```bash
python3 -m venv .venv-desktop
.venv-desktop/bin/python -m pip install -e '.[dev,desktop-build]'
.venv-desktop/bin/python -m src.desktop
```

首次启动可选择已有`workbench.db`，或者在`~/Library/Application Support/LiveCommerceWorkbench/workbench.db`创建新账本。桌面配置`desktop.json`只保存账本绝对路径；账本移动或丢失时不会静默创建替代库。

开发验证时也可明确指定已有账本：

```bash
.venv-desktop/bin/python -m src.desktop --db /absolute/path/to/workbench.db
```

同一账本一次只能由一个桌面版或浏览器工作台进程打开。MCP是只读进程，不参与这把工作台锁。

## 数据位置与MCP

应用菜单“数据位置”显示当前账本绝对路径。Codex和Claude Code中`livecommerce-m3-readonly`的`--db`必须指向同一文件；桌面版不会自动修改用户级客户端配置。

日志位于`~/Library/Application Support/LiveCommerceWorkbench/desktop.log`，只记录启动、退出和错误，不记录订单、费用备注或账本内容。

## 构建macOS应用

在已经安装`desktop-build`依赖的隔离环境中执行：

```bash
.venv-desktop/bin/python packaging/macos/setup.py py2app
```

输出位于`packaging/macos/dist/直播经营复盘工作台.app`。当前构建只做本机临时签名；未完成Apple开发者签名、公证、干净机器和财务BP试用前，只能称为开发验证版，不能声称可在任意Mac分发运行。

## 当前验收重点

- 从Finder启动后进入月份总览。
- 中文路径下选择账本、导入Excel，并验证取消文件选择。
- 保存模板、CSV和正式Excel，验证取消及同名文件处理。
- 完成M3概览、证据、比较、条件测算和复制引用。
- 操作进行中关闭窗口不会截断写入；退出后端口与锁释放。

## GitHub Release 打包

在源码提交、应用构建及验证完成后执行：

```bash
python3 packaging/macos/package_release.py --tag v0.1.0-build5
```

输出位于被 Git 忽略的 `release-artifacts/`：完整源码 ZIP、macOS 产品 ZIP 及 SHA256 校验清单。产品 ZIP 包含应用、完整源码压缩包、使用手册、标准模板和合成账例。源码严格来自当前 Git 提交，不拷贝本地账本、日志或用户配置。
