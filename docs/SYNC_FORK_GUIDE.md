# Fork 自动同步配置指南

## 📋 概述

本工作流自动将你的 fork 仓库与上游主仓库 (`ZhuLinsen/daily_stock_analysis`) 保持同步。

## 🔧 配置步骤

### 步骤 1：创建 Personal Access Token (PAT)

1. 访问 GitHub 设置: https://github.com/settings/tokens
2. 点击 **"Generate new token"** -> **"Classic"**
3. 配置 Token:
   - **Note**: `Sync Fork Token`
   - **Expiration**: 选择过期时间（推荐 90 天或更久）
   - **Scopes**: 勾选以下权限
     - ✅ `repo` - 完全仓库权限
     - ✅ `workflow` - 更新 GitHub Actions 工作流
4. 点击 **Generate token**
5. **复制生成的 token**（只显示一次！）

### 步骤 2：添加到仓库 Secrets

1. 打开你的 fork 仓库页面
2. 进入 **Settings** -> **Secrets and variables** -> **Actions**
3. 点击 **New repository secret**
4. 配置:
   - **Name**: `PAT_TOKEN`
   - **Secret**: 粘贴刚才复制的 token
5. 点击 **Add secret**

### 步骤 3：添加上游 Remote（本地开发时使用）

```bash
# 在本地仓库添加 upstream
git remote add upstream https://github.com/ZhuLinsen/daily_stock_analysis.git

# 验证
git remote -v
# 应该显示:
# origin    git@github.com:aoshuangzhitou/daily_stock_analysis.git (fetch)
# origin    git@github.com:aoshuangzhitou/daily_stock_analysis.git (push)
# upstream  https://github.com/ZhuLinsen/daily_stock_analysis.git (fetch)
# upstream  https://github.com/ZhuLinsen/daily_stock_analysis.git (push)
```

## 🚀 使用方式

### 自动同步
- 每天 **UTC 00:00**（北京时间 08:00）自动检查并同步
- 如无更新，跳过同步

### 手动同步
1. 打开仓库页面
2. 点击 **Actions** 标签
3. 选择 **Sync Fork with Upstream**
4. 点击 **Run workflow**

## ⚠️ 冲突处理

如果同步出现冲突，GitHub Actions 会失败并提示解决步骤。

### 本地解决冲突：

```bash
# 1. 获取上游更新
git fetch upstream

# 2. 切换到 main 分支
git checkout main

# 3. 合并上游变更
git merge upstream/main

# 4. 解决冲突（如果有的话）
# 编辑冲突文件，然后：
git add .
git commit -m "Merge upstream changes"
git push origin main
```

### 丢弃本地修改（慎用）：

```bash
# 强制同步上游（会丢失本地修改！）
git fetch upstream
git checkout main
git reset --hard upstream/main
git push origin main --force
```

## 📊 同步状态检查

### 查看同步历史
- 仓库页面 -> **Actions** -> **Sync Fork with Upstream**

### 查看更新日志
同步成功后，可以在 commit 历史中看到类似：
```
Sync from upstream ZhuLinsen/daily_stock_analysis
```

## 🔧 自定义配置

编辑 `.github/workflows/sync-fork.yml` 修改以下变量：

```yaml
env:
  UPSTREAM_REPO: ZhuLinsen/daily_stock_analysis  # 上游仓库
  UPSTREAM_BRANCH: main                           # 上游分支
  FORK_BRANCH: main                               # 你的 fork 分支
```

### 修改同步频率
```yaml
on:
  schedule:
    # 每 6 小时运行一次
    - cron: '0 */6 * * *'
    # 或每周一运行
    - cron: '0 0 * * 1'
```

Cron 表达式格式：
```
分钟 小时 日期 月份 星期
```

## 🔔 添加飞书通知（可选）

在仓库 Secrets 中添加 `FEISHU_WEBHOOK`，然后修改工作流：

```yaml
- name: Notify Feishu
  if: steps.sync.outputs.sync_status == 'success'
  run: |
    curl -X POST ${{ secrets.FEISHU_WEBHOOK }} \
      -H 'Content-Type: application/json' \
      -d '{
        "msg_type": "text",
        "content": {
          "text": "✅ Fork 同步成功\n仓库: ${{ github.repository }}\n上游: ${{ env.UPSTREAM_REPO }}"
        }
      }'
```

## ❓ 常见问题

### Q: Token 过期了怎么办？
A: 重新生成 PAT，然后更新仓库 Secrets 中的 `PAT_TOKEN`。

### Q: 同步失败显示 "Permission denied"
A: 检查 PAT 是否有 `repo` 和 `workflow` 权限。

### Q: 如何避免同步时覆盖我的修改？
A: 工作流使用 `merge` 策略，会保留你的修改。如有冲突需要手动解决。

### Q: 只想同步特定文件？
A: 目前不支持。建议：
1. 在独立分支开发
2. 或手动 cherry-pick 上游更新

## 📞 支持

如有问题，可以：
1. 查看 Actions 日志获取详细错误信息
2. 在 GitHub Issues 中提问
3. 参考 GitHub 官方文档: https://docs.github.com/en/actions
