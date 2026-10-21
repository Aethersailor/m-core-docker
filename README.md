> 本工程已迁移至 [Container Forge 的 mihomo 工程](https://github.com/Aethersailor/container-forge/tree/main/mihomo)。后续更新在新仓库维护，镜像地址和标签保持不变。本仓库保留历史记录。

# m-core-docker

自动同步 [mihomo 官方](https://github.com/MetaCubeX/mihomo)的正式版和 Alpha 并打包官方二进制为 Docker 镜像，为 Docker 补齐 amd64 v1、v2、v3。不修改核心代码。

## 镜像地址

[Docker Hub](https://hub.docker.com/r/aethersailor/mihomo) 和 [GHCR](https://github.com/Aethersailor/m-core-docker/pkgs/container/mihomo) 的镜像内容相同，可任选其一：

```text
aethersailor/mihomo
ghcr.io/aethersailor/mihomo
```

## 选择标签

| 标签 | 用途 |
| --- | --- |
| `latest` | 最新正式版 |
| `v1.x.x` | 指定已同步的正式版，例如 `v1.19.32` |
| `Alpha` | 最新 Alpha，注意大写 `A` |

支持 **amd64 v1/v2/v3、386、arm64、arm/v7**。支持自动匹配的 Docker 会选择 CPU 能运行的最高指令等级，无需手动指定平台。

旧版 Docker 或需要固定等级时，可使用 `latest-amd64-v1`、`latest-amd64-v2`、`latest-amd64-v3`。`Alpha` 和版本号也提供相同后缀，例如 `Alpha-amd64-v2`。所选等级须由 CPU 支持。

## 使用

先准备 mihomo 的 `config.yaml`，再创建 `compose.yaml`：

```yaml
services:
  mihomo:
    image: aethersailor/mihomo:latest
    restart: unless-stopped
    ports:
      - "127.0.0.1:7890:7890"
    volumes:
      - ./config.yaml:/root/.config/mihomo/config.yaml:ro
```

这个示例向本机开放代理端口，配置中需要启用 `mixed-port: 7890` 和 `allow-lan: true`。

```sh
docker compose up -d
```

使用 Alpha 时，将标签改为 `Alpha`。使用 GHCR 时，将镜像名改为 `ghcr.io/aethersailor/mihomo`。TUN、透明代理等用法参见 [mihomo 文档](https://wiki.metacubex.one/)。配置目录和启动参数与官方镜像一致。

更新镜像：

```sh
docker compose pull
docker compose up -d
```

每 30 分钟检查一次官方更新，实际同步时间可能延迟。
