# 仿真环境与资产

策略 Python 与仿真 Python 分开：根目录的 `requirements-policy.txt` 用于模型调用、输入构建和离线测试；`--sim-python` 指向已有 RoboDojo / IsaacSim 环境。这样不同协作者可以复用各自机器上的仿真安装。

## 复用已有环境

原实验使用 Python 3.11、IsaacSim 5.1.0、PyTorch 2.7.0 + CUDA 12.8，以及该版 RoboDojo 所需的 IsaacLab 和 CuRobo。详细的原运行环境记录在 [运行时清单](../release/manifests/worker-runtime.json) 的 `runtime_versions` 中。

需要可用的 NVIDIA 驱动、`nvidia-smi`，以及能运行 IsaacSim 的 GPU 和系统库。每场启动要求输出位置至少剩余 8 GiB；日志和视觉历史持续增长，按任务另留空间。视频生成需要在 PATH 中提供 `ffmpeg` 和 `ffprobe`。

先恢复源码：

```bash
python -B scripts/restore_f_sources.py
```

已有固定提交的干净 RoboDojo 检出目录也可通过 `--robodojo-root /path/to/RoboDojo` 指定。启动器会校验根提交与 XPolicyLab 子模块提交，以及源码是否干净。

## 新机器安装

安装流程以固定源码中的 [README](../dependencies/RoboDojo/README.md)、[Dockerfile](../dependencies/RoboDojo/Dockerfile) 和 [安装脚本](../dependencies/RoboDojo/scripts/install.sh) 为参考，也可参照 [RoboDojo 安装文档](https://robodojo-benchmark.com/doc/usage/install-and-download/)。这些本地链接在恢复源码后可访问。

初始化仿真子模块时保留提交 pin：

```bash
git -C dependencies/RoboDojo submodule update --init \
  third_party/IsaacLab third_party/curobo
```

原安装脚本的 `submodules` 步骤使用 `--remote` 更新子模块。复现本仓库时应使用上面的固定提交初始化方式，保留已恢复的 XPolicyLab。环境安装中生成的缓存和下载资源放到源码目录外，运行前确认源码校验通过。

本仓库提供的 Git bundle 不含 IsaacSim 二进制、IsaacLab/CuRobo 的完整安装环境或仿真资产。这些内容按其各自安装与访问方式准备。

## 资产

`--assets` 指向包含下列结构的绝对路径：

```text
Assets/
  Robots/
  Object/RoboDojo/
  Material/
  Eval_Layout/RoboDojo/arx_x5/0/
    general_pickup_20.json
    ...
```

可以复用团队已有的 RoboDojo 资产，或按上游安装文档获取。固定 RoboDojo 中的 `scripts/init_assets.sh` 提供下载入口；下载位置和缓存应放在依赖源码目录外，再通过 `--assets` 指定。

历史实验记录的资产 revision 为 `91f76c28d93dd20c5fa46ce6a5a1d96a4f384acd`，详见 [依赖记录](../release/dependencies.json)。若使用其他资产版本，在实验记录中注明。启动器会保存选中布局的 SHA-256，逐场创建机器人 YAML 路径 overlay，原始资产保持原样。

`release/DEPLOYMENT.md` 和 `release/manifests/` 另保留早期 random12 发布包的私有运行时恢复方案；其中资产清单覆盖该历史队列，不能替代当前七任务所需的完整资产。新入口运行七任务时使用上述 `scripts/run_f.py`，并显式提供当前机器的运行环境与资产路径。

## 运行前确认

```bash
python -B scripts/check_f.py
python -B scripts/run_f.py --help
nvidia-smi
/absolute/path/to/simulator/bin/python --version
```

准备 `local/api.toml` 和自己的凭据后，按根 README 的命令启动。首次迁移时先选择一项任务的单个布局，确认模型请求、原生结果和视频都正常，再分配后续实验。
