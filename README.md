# mihomo-docker

自动跟随 [MetaCubeX/mihomo](https://github.com/MetaCubeX/mihomo) 的正式版和 Alpha 更新，将官方二进制重新打包为 Docker 镜像，补齐 amd64 的 v1、v2、v3 指令集等级。不修改、不重新编译 mihomo 核心。本项目为社区维护，与 MetaCubeX 官方没有隶属关系。

## 镜像地址

两个镜像仓库使用相同标签和内容：

```text
ghcr.io/aethersailor/mihomo-docker
docker.io/aethersailor/mihomo-docker
```

| 标签 | 内容 |
| --- | --- |
| `latest` | 最新正式版 |
| `v1.x.x` | 对应的官方正式版，例如 `v1.19.32` |
| `Alpha` | 官方 `Prerelease-Alpha` 当前发布的二进制 |
| `<标签>-amd64-v1` | 手动指定 amd64 v1 |
| `<标签>-amd64-v2` | 手动指定 amd64 v2 |
| `<标签>-amd64-v3` | 手动指定 amd64 v3 |

`Alpha` 的大小写与官方 Docker 标签一致。首次运行同步当前最新正式版和 Alpha；此后保留已同步的版本标签，不自动回填全部历史版本。上游重新上传正式版资产或本项目打包代码更新时，相同版本标签可以重新打包；需要固定镜像内容时使用 digest。

## 平台

| 镜像平台 | 官方核心 |
| --- | --- |
| `linux/amd64` | amd64-v1 |
| `linux/amd64/v2` | amd64-v2 |
| `linux/amd64/v3` | amd64-v3 |
| `linux/386` | 386 |
| `linux/arm64` | arm64 |
| `linux/arm/v7` | armv7 |

同一个主标签包含以上六个平台。支持 amd64 变体自动匹配的 Docker 会按 CPU 能力选择镜像中最高兼容等级。传统镜像存储路径从 [Docker 23.0](https://docs.docker.com/engine/release-notes/23.0/) 开始支持此功能；containerd 镜像存储路径的相关选择问题在 [Docker 29.6.0](https://docs.docker.com/engine/release-notes/29/#2960) 修复。

保留自动选择时，不要传入 `--platform=linux/amd64`，Compose 中也不要固定 `platform: linux/amd64`。虚拟机按虚拟 CPU 实际暴露的能力选择。旧版 Docker 或需要明确指定等级时，使用辅助标签，例如 `Alpha-amd64-v2`；所选等级必须由运行环境支持。

## 使用

配置目录和入口与官方镜像保持一致：`/root/.config/mihomo/` 和 `/mihomo`。基础镜像为 Alpine，包含 CA 证书、时区数据、iptables 和官方来源的 `geoip.metadb`、`geoip.dat`、`geosite.dat`。

先在本地 `config.yaml` 中准备 mihomo 配置，再创建 `compose.yaml`：

```yaml
services:
  mihomo:
    image: ghcr.io/aethersailor/mihomo-docker:latest
    container_name: mihomo
    restart: unless-stopped
    ports:
      - "127.0.0.1:7890:7890"
    volumes:
      - ./config.yaml:/root/.config/mihomo/config.yaml:ro
```

示例仅向本机开放混合代理端口，配置中需要启用 `mixed-port: 7890` 和 `allow-lan: true`。TUN、透明代理和其他部署方式按 [mihomo 文档](https://wiki.metacubex.one/) 配置相应设备、网络和权限。

```sh
docker compose pull
docker compose up -d
```

使用 Docker Hub 时，将 `image` 改为 `aethersailor/mihomo-docker:latest`。使用 Alpha 时，将标签改为 `Alpha`。

## 自动同步

GitHub Actions 每 30 分钟检查一次上游；调度实际启动时间可能延迟。推送到 `main` 或手动运行 **Sync mihomo images** 也会触发检查。手动运行可以选择 Release、Alpha 或两者，也可以强制重新打包。

同步流程：

1. 读取上游发布信息和 `version.txt`，锁定官方版本与源码提交。
2. 确认六个核心资产齐全，并校验 GitHub 提供的 SHA-256 和文件大小。Alpha 正在更新、文件混版或校验失败时停止，保留原标签。
3. 使用相同二进制构建镜像，先按 digest 推送到 GHCR 和 Docker Hub。
4. 核对两边的平台清单，逐个平台检查容器内二进制 SHA-256、版本输出和最小配置解析。ARM 平台通过 QEMU 执行。
5. 验证成功后更新对应标签，再次核对两个镜像仓库。正式版与 Alpha 独立同步。

两个镜像仓库之间没有跨仓库事务。如果标签更新期间一边的服务失败，Actions 会报错；后续同步会核对并修复未完成的标签。没有更新且所有主标签和辅助标签完整时，跳过构建。

部署本仓库时，需要在 GitHub Actions Secrets 配置：

| Secret | 用途 |
| --- | --- |
| `DOCKERHUB_USERNAME` | Docker Hub 用户名 |
| `DOCKERHUB_TOKEN` | 具有目标镜像仓库写权限的 Docker Hub 访问令牌 |

GHCR 使用仓库自带的 `GITHUB_TOKEN`，无需另配 PAT。首次发布后需要确认 GHCR 包为公开可见。发布工作流只在非 PR 事件运行，第三方 Action 固定到提交 SHA。

## 来源与验证

镜像内的 `/usr/share/mihomo/upstream.json` 记录版本、上游提交、二进制及数据文件的 SHA-256、源码链接和源码压缩包地址。OCI 标签记录本仓库打包提交和上游核心提交；Actions 运行摘要记录发布的镜像索引 digest。

```sh
docker run --rm --entrypoint cat ghcr.io/aethersailor/mihomo-docker:latest /usr/share/mihomo/upstream.json
docker buildx imagetools inspect ghcr.io/aethersailor/mihomo-docker:latest
```

本地测试只依赖 Python 标准库：

```sh
python3 -m unittest discover -s tests -v
python3 scripts/sync.py --channel alpha --plan
```

上游 mihomo 和本仓库使用 GPL-3.0；许可证见 [LICENSE](LICENSE)。mihomo 对应源码可从镜像记录的官方提交获取。本仓库不包含代理订阅或个人配置。
