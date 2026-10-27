# DeepTutor Desktop 的 Magpie Agent 插件

安装后在 Magpie 的 **Agent → DeepTutor Desktop** 选择模型或路由组。
插件使用 Magpie 的标准 Agent 接口，不需要改 Magpie，也不需要登录第二份账号。

```powershell
pwsh -File .\install.ps1 -Magpie 'E:\edge\magpie-windows-amd64.exe'
```

安装脚本读取桌面版 `desktop.json` 的 `home`，绑定已选的数据目录。
在 Magpie 选择模型后，关闭并重新打开 DeepTutor，正在运行的后端会使用新设置。
在 Magpie 断开连接会恢复接入前的主模型配置；其他服务、工作区、资料和聊天记录不受影响。
安装时会建立独立的 Magpie 主模型配置；已有模型配置会完整保留。
如果在桌面设置中更换数据目录，重新运行安装脚本即可重新绑定。

插件不保存上游密钥，使用 Magpie 为 `deeptutor` 提供的本机网关标识。
用量和请求记录会归到 `deeptutor`。

`npm test` 使用真实 Magpie 命令行和隔离的配置目录，检查连接、切换模型、恢复原配置和其他服务的保留。
测试可通过 `MAGPIE_EXE` 指定 Magpie 程序。
