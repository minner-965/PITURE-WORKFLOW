# 商品图程序与工作流

本仓库包含两个压缩包：`program-source.zip` 为程序、配置、模板、提示词、文档和自研节点源码；`latest-workflows.zip` 为商品主图通用 UI/API 工作流、快餐盘最终交付十五张的来源工作流链，以及火锅盖碗 v08 的 UI/API 和相关文本记录。`MANIFEST.txt` 列出压缩包文件；`SHA256SUMS` 用于核对下载完整性。

将两个 ZIP 解压到同一个项目目录，合并同名目录。解压后在项目根目录运行 `python3 -m fission --help`，其主图入口包括 master、scene、food、multishop、layout、export、report。程序依赖 Python、既有 ComfyUI、对应模型与节点；生成使用本地 ComfyUI 服务。三个自研节点源码位于代码包 `custom_nodes/`，需放到目标 ComfyUI 的同名目录并按其实际依赖配置。字体文件、模型、图片、实拍素材、运行缓存、海量日志及历史备份均未打包，少量必要来源记录保留。字体名称与授权参见源码包中的使用文档及原项目 `fission/fonts/manifest.json`；目标环境需自行准备相应字体。第三方节点及模型需要按工作流的节点类型和模型名称另行准备。

工作流中的绝对路径、模型名、输入图和本机服务地址指向原环境，迁移后需核对和修改；示例图片及运行数据不在此包内，因而不能直接复现样图。原项目路径为 `/Users/luohuiwu/codex/ComfyProductPipeline`，原 ComfyUI 节点路径为 `/Users/luohuiwu/ComfyUI-Installs/E-commerce image workflow/ComfyUI/custom_nodes`。相关记录中的历史路径仅供追溯。

当前状态：v08 已获用户确认；v09 已退回，逐碗换色路线已撤回，mixed_v01 失败，新的区域单采样方案尚未提交。v08 的确认仅适用于该样图，不代表混色方案或后续图已通过。通用工作流保留成对 UI/API；迁移后的运行和视觉效果尚需在目标环境验证。程序入口整合已完成，但商品文件夹自动识别、SKU 文案自动生成及详情图生成仍属后续工作。

快餐盘十五张的最终交付来源清单及对应最终 UI/API 工作流已收录；已沿来源记录收录能够追踪的上游商品生成和场景工作流；具体链路及缺口见包内 workflow-index.json。关联图片未收录。工作流通过状态仅对应各自审核的图片。代码中区域联合采样等新分支仍属试验，尚未提交验证；部分历史脚本依赖本机图片和运行目录，保留供追溯，迁移时须补齐资料。

B05 的 approved_batch_v04 曾被退回，包内仅保留其作为实际上游输入的 UI/API 追溯记录；它不是最终入口或已通过成品。
