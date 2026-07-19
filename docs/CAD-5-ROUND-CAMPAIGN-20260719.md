# AutoCAD MCP 五轮复杂项目验证计划

## 目的与边界

本计划用于验证 AutoCAD MCP 的真实原生 3D 建模、文档身份、事务、固定相机预览、语义 DRC 和交付证据。五轮项目故意覆盖不同几何类型和失败模式；每轮都必须在独立文档和独立输出目录中执行。

本文件是测试设计，不会自动启动、关闭、最小化或激活用户的 AutoCAD。真实 CAD 回归必须显式设置 `LIVE_AUTOCAD=1`，并通过 MCP 的启动门、文档锁和窗口策略；默认只运行离线契约测试。

## 统一执行协议

### 全局单位、坐标和文件布局

- 单位：mm；角度：deg；质量和材料不是本轮验收内容时标为 `NOT_EVALUATED`。
- 全部模型使用右手坐标：X=宽度/左右，Y=深度/前后，Z=高度；主基准为 `DATUM_XY`（Z=0）、`DATUM_YZ`（X=0）、`DATUM_XZ`（Y=0）。
- 每轮根目录：`D:/CAD-Automation/campaign-20260719/R{n}-{slug}/`。
- 固定子目录：`specs/`、`models/`、`drawings/`、`previews/`、`reports/`、`outputs/`、`journal/`。
- 源权威：`specs/project.json` + MCP 创建响应；交付副本必须记录 `source_authority`、`doc_id`、`revision` 和内容哈希。

### 统一阶段

1. **Preflight**：读取 MCP/AutoCAD 健康状态、PID/HWND、前台窗口、后端版本、单位、输出锁；已有用户文档只读绑定，不自动改变其路径、视觉样式或窗口状态。
2. **Create/Bind**：`drawing.create` 或 `drawing.open` 后立即核验 `doc_id`、`requested_path`、`active_doc_id`、`active_path`、`revision`；后续每个修改请求携带 `doc_id` + `expected_revision`。
3. **Transaction**：`transaction.begin`；按骨架、主件、细节、语义、视图五个小批次创建。任一实体的请求/回读不一致，返回 `E_POSTCONDITION_MISMATCH` 并回滚整个批次。
4. **Readback**：每个实体记录类型、句柄、图层、语义标签、包围盒、体积/面积、闭合状态和 `requested/actual/diff`。
5. **Audit**：运行几何 DRC、语义 DRC、文档身份、图层和拓扑审计；未解释悬空端点、交叉和近接间隙均为 FAIL。
6. **Preview**：固定相机输出 `front/right/top/iso`，需要运动的项目再输出 `rotated_iso/section/exploded`。预览使用 `ShadedWithEdges` 或 `Conceptual`，白底、强制覆盖、内容哈希和非空像素检查。
7. **Deliver**：事务提交后保存 FC/DWG/DXF/STEP（工具支持的格式）和报告；PDF 的纸张、方向、比例从实际 PDF MediaBox 回读，标题栏声明不一致则 FAIL。
8. **Cleanup**：删除临时图形和测试预览，保留 `reports/round.json`、失败实体快照、MCP journal 和哈希清单。禁止删除用户文件或用户原文档。

### 通用放行门槛

- `document_identity`: PASS；任何文档 ID 或 revision 不匹配立即停止。
- `transaction`: PASS；故意失败的批次实体数增量必须为 0。
- `geometry_drc`: PASS；零长度、重复、自交、无效 B-rep 均为 0。
- `semantic_drc`: PASS；每个实体必须有 `component_id`、`design_role`、`view_id`、`source_authority`；开放端点必须有 `intentional_open_end`。
- `topology`: PASS；未解释悬空端点=0，近接未接点=0，非预期交叉=0。
- `view_framing`: PASS；非空像素比例 `>=0.08`，裁切=false，左右/上下内容边距差 `<=3%`。
- `delivery`: PASS；所有声明存在的文件可读，临时输出不留半成品，内容哈希稳定。

## 五轮项目

### R1：参数化液压阀块（机械零件）

**验证重点**：原生 3D 基本体、孔系、倒角/圆角、语义面/边选择、壁厚和端口占位的边界。

**统一参数（`specs/project.json`）**

```json
{
  "id": "R1-hydraulic-manifold",
  "stock": [120, 80, 60],
  "edge_fillet": 6,
  "edge_chamfer": 2,
  "mount_hole": {"diameter": 10, "spacing_x": 90, "spacing_y": 50, "edge_offset": 15},
  "cross_bores": [{"axis": "X", "diameter": 18, "z": 35}, {"axis": "Y", "diameter": 14, "z": 45}],
  "ports": [{"name": "P", "axis": "X", "diameter": 16, "authority": "supplier_drawing"}, {"name": "T", "axis": "Y", "diameter": 16, "authority": "supplier_drawing"}]
}
```

**创建步骤**：圆角方块/拉伸毛坯 → 语义选择四条外侧竖边做 R6 → 上下安装孔阵列 → X/Y 交叉孔布尔差集 → P/T 端口受控占位 → C2 倒角 → 截面视图标注孔轴。

**验收**：实体数和孔数与参数表一致；四条圆角半径读回均为 6±0.01；倒角距离 2±0.01；孔轴交点误差 ≤0.05；B-rep 有效；截面中没有封闭孔被误填充；供应商端口不得生成制造尺寸。

### R2：两级斜齿减速器（齿轮传动）

**验证重点**：同轴/中心距关系、旋转体/扫掠齿形、装配间隙、运动包络和多视图共同参数源。

**统一参数**：模数 `m=2.5/3.0`，齿数 `z1/z2/z3/z4=20/80/20/100`，螺旋角 `15°`，齿宽 `b1=32,b2=40`，轴间中心距 `a1=125,a2=180`，轴径 `d=25/35/45`，设计转矩 `858 N·m`，轴承座间隙 `1.5`。

**创建步骤**：轴线骨架 → 齿轮基体（旋转/拉伸）→ 参数齿槽阵列或受控概念齿形 → 两级轴和轴承座 → 箱体基准 → 布尔装配 → 旋转运动配置 `0/45/90/180°` → 干涉采样 → 等轴测、剖视和爆炸视图。

**验收**：四个齿轮节圆半径与 `m*z/2` 误差 ≤0.02；中心距误差 ≤0.05；轴线同轴误差 ≤0.05；旋转配置只改变变换、不复制实体；静态和运动包络均无未解释干涉；齿槽若为概念几何必须标 `concept`，不得宣称制造齿形。

### R3：铝合金电机安装箱体（箱体/支架）

**验证重点**：抽壳/壁厚、加强筋、安装基准、拔模/加工余量标签和交付图纸比例一致性。

**统一参数**：外形 `260×180×120`，名义壁厚 `4`，底板厚 `8`，R8 外圆角、C1.5 加工倒角，四角安装孔 `Ø9`（孔距 `220×140`），六条加强筋厚 `5`、高 `28`，电机法兰 `Ø110`、孔距 `90`。

**创建步骤**：外壳圆角方块 → `shell` 保留底面 → 筋板拉伸并阵列 → 法兰和孔系布尔 → 底部支架与定位销 → 语义基准面/特征 ID → 壁厚、拔模和干涉分析 → A3 横向制造视图及剖面。

**验收**：最小壁厚 ≥3.8 且偏差 ≤0.2；加强筋与壳体相交长度 ≥2；孔位误差 ≤0.05；法兰同心度 ≤0.05；拔模不足仅可 `NOT_EVALUATED` 并列风险；A3 横向 MediaBox 与标题栏比例一致，禁止把 FIT 标为 1:1。

### R4：便携式旋钮控制器外壳（消费品外壳）

**验证重点**：消费品形体、分型线、按键/USB 受控模块、圆角连续性、固定相机着色和外观审查。

**统一参数**：外壳 `160×90×32`，外 R12，内壁 `2.2`，分型线 Z=16；旋钮 Ø42×12，显示窗 `70×22×1.5`，USB-C 仅创建 `TBD/supplier_controlled` 占位，四个防滑脚 Ø12×2；表面颜色 `body=dark gray, accent=teal, metal=brushed`（只作可视标签）。

**创建步骤**：上/下壳体圆角与抽壳 → 分型间隙 `0.3` → 旋钮和轴套 → 显示窗凹槽 → USB-C 受控模块占位 → 防滑脚阵列 → `ShadedWithEdges` 固定前/右/顶/ISO 预览 → 外观、人体工学、线缆和适配器间隙审查。

**验收**：内壁全局 ≥2.0；分型间隙 0.25~0.35；旋钮轴线与壳体基准同轴 ≤0.05；USB 开孔不得标注生产尺寸；圆角读回 R12±0.05；四视图非空且不裁切；产品审查不能由几何 PASS 推导，至少 `appearance_review` 和 `ergonomics_review` 有证据或 `NOT_EVALUATED`。

### R5：三轴云台相机机构（运动装配）

**验证重点**：组件实例、旋转轴/限位、配置变换、连续扫掠、爆炸视图和运动叠影语义。

**统一参数**：底座 `Ø90×18`；偏航轴 Z，限位 `±160°`；俯仰轴 Y，限位 `-45~+90°`；滚转轴 X，限位 `±180°`；相机包络 `120×70×65`；最小静态间隙 `2`；线缆包络半径 `10`；配置角度分别为 `[0,0,0]`、`[45,20,0]`、`[120,-30,90]`、`[-90,60,-120]`。

**创建步骤**：固定底座 → 中央脊柱 → 三个旋转接口和轴承占位 → 相机模块 → `component.instance` 与 mates → 四个 configuration → `motion.sweep_interference`（每轴 9 点）→ intentional motion overlay → ISO/rotated ISO/section/exploded 预览。

**验收**：每个组件只有一个源特征 + 实例变换；DOF 与三轴一致；限位外角度被拒绝；扫掠采样覆盖所有配置；最小间隙 ≥2；运动叠影标记 `design_role=motion_overlay` 且不触发普通交叉 FAIL；爆炸视图偏移可逆；所有相机返回实际参数、分辨率、非空比例和哈希。

## 每轮记录模板

每轮保存 `reports/round.json`，至少包含：

```json
{
  "round_id": "R1-hydraulic-manifold",
  "started_at": "ISO-8601",
  "status": "PASS|FAIL|BLOCKED",
  "environment": {"mcp_version": "", "backend": "", "acad_pid": "", "acad_hwnd": "", "window_mode": "preserve", "foreground_before": "", "foreground_after": ""},
  "document": {"doc_id": "", "requested_path": "", "active_path": "", "revision_start": 0, "revision_end": 0},
  "parameters_hash": "",
  "transactions": [{"batch": "", "status": "", "entity_count_before": 0, "entity_count_after": 0, "rollback_verified": false}],
  "entities": [{"handle": "", "type": "", "layer": "", "component_id": "", "design_role": "", "bounds": {}, "volume": null, "requested": {}, "actual": {}, "diff": {}}],
  "audits": {"geometry_drc": {}, "semantic_drc": {}, "topology": {}, "document_identity": {}},
  "views": [{"name": "iso", "visual_style": "ShadedWithEdges", "camera": {}, "metrics": {}, "sha256": ""}],
  "reviews": {"appearance_review": "PASS|FAIL|NOT_EVALUATED", "ergonomics_review": "PASS|FAIL|NOT_EVALUATED", "clearance_review": "PASS|FAIL|NOT_EVALUATED"},
  "deliverables": [{"path": "", "format": "DWG|DXF|STEP|PDF|PNG", "exists": false, "bytes": 0, "sha256": "", "media_box": null}],
  "lessons": [],
  "next_round_changes": [],
  "cleanup": {"temporary_deleted": [], "evidence_retained": [], "errors": []}
}
```

## 轮次复盘规则

每轮结束必须写出三类结论：

1. **事实**：请求值、读回值、审计结果和截图指标，不以“看起来正确”替代。
2. **根因**：将问题归因到参数、MCP 契约、AutoCAD/COM、预览或交付层之一，并附 journal 证据。
3. **下一轮改进**：最多三项可验证改动，写明预期指标和回归测试；同一故障连续两次出现则切换后端或标记 `BLOCKED`，禁止继续盲画。

五轮结束汇总 `reports/campaign-summary.json`：通过率、每类错误频次、平均回读差异、事务回滚成功率、启动门耗时、前台抢占次数（目标为 0）、预览裁切次数和未解释拓扑问题数。
