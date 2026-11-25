# 污水排放批（effluent）

数据工程母仓：处理厂采样入库 → 超标旁路挂起 → 准排放裁定 → 封批升代 → 排放许可对账。

## 骨架一句话

采样批次入库后裁定超标才准排放封批，驳回进旁路挂起，指标血缘与排放许可对账。

## 一键起环境

```bash
docker compose up -d --build
```

浏览器访问 http://localhost:11600

- 管理账号：`admin` / `admin123`
- 读写账号：`writer` / `writer123`
- 只读账号：`viewer` / `viewer123`

## 本地开发

```powershell
cd projects/14-effluent
$env:PYTHONPATH="backend"
python -m uvicorn app:app --app-dir backend --reload --port 8000
```

## 自测

```powershell
$env:PYTHONPATH="backend"
python -m pytest
python scripts/count_loc.py
```

## effluent-03 旁路禁封口径（当班封死）

正确性总口径：**旁路开放态与封批升代两边不得混用**。仅允许以下两种写法，无第三写法；
禁止只改封批页角标颜色。偏离一律记“旁路禁封偏航”并拒写。

- **甲路径（封批写口当场拒写）**：`POST /api/seals` 检测到 `exceed_holds` 仍有
  `status='开放'` 挂起时，当场返回 400，`discharge_seals` 正式区零增、
  `sample_batches` 代次与批态零改，并独立事务写 `seal_blocked_bypass_open` 偏航审计痕。
- **乙路径（影子快照草稿 + 原子升正）**：封批意图只能经 `POST /api/seals/draft`
  落 `status='影子草稿'`（不升代、不改批态）；`POST /api/seals/promote` 在旁路已闭时
  同一事务原子升正（草稿转 `已封批` + 升代 + 批态翻转），旁路仍开放则草稿置
  `草稿作废`、正式区零改并留偏航痕。

互斥与同源：

- 超标页/采样页回传 `bypass_open`（以开放挂起实际存在为准，非只看批态），
  封批页升代键对旁路开放批禁写，对账页“已封灯”与封批计数只认 `status='已封批'`，
  影子草稿与草稿作废一律不计；证明体 `sealed` 与 `bypass_open` 互斥。
- 两路会话同批抢封批须占 `sample:{id}` 批锁：先到持锁，后到返回 409，
  争用痕由独立事务落 `contention_log`，不随业务回滚丢失（后到只记争用痕）。
- 封批页、超标页、采样页、血缘页、对账页五页核账实同源。

验收闸口（失败测链禁止跳过）：

- 三红测试分属不同服务：`tests/test_effluent_03_jia.py`（封批正式快照）、
  `tests/test_effluent_03_yi.py`（对账已封误标/草稿升正）、
  `tests/test_effluent_03_zheng.py`（批锁争用）；禁止单点旁路标志读取救齐三红。
- 双会话并发：`python scripts/ping_seal_lock.py`（打同批封批，验串行化与争用痕）。

