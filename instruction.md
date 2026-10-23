# 储能站点的夜间结算与调度交接

区域调度组在夜间结算时发现，几座变电站的计量总量与储能单元上报的指令曲线对不上。运维交接单保留了单元编号,容量和逐时指令，站点归属与充放电损耗在一次系统迁移中未能随单归档。值班工程师需要把站点归属和损耗恢复出来，再按当天调度请求生成一份可复核的交付记录。

交接脚本名为 energy_ledger.py，读取一份场景 JSON 并写入交付目录。

成功时命令保持静默，标准输出与标准错误都不写下任何字符。

储能交接单把 schema_version,run_id,case_id,units,stations,dispatch_request 放在同一层。

每条储能单元记录只写 unit_id,profile,capacity 三个槽位。

每个变电站条目写 station_id 与 grid_energy 两个槽位。

每条调度请求写 station_id 与 energy_mwh 两个槽位。

profile 与 grid_energy 各有 12 个数值。

unit_id 与 station_id 不得重复。

所有数值必须有限，布尔值不能顶替整数位置。

交接单允许 6 个站点、18 个单元。

station 数量必须整除 unit 数量。

每个 station 的单元数必须相同。

值班交接会产生一份报告、一个追加账本和一份恢复记录。

最终文本不使用空格分隔，UTF-8 编码，文件只以一个换行收尾。

报告外层七个键依次是 schema_version,case_id,run_id,assignments,losses,dispatch,summary。

归属行交给下游的字段是 unit_id 和 station_id。

损耗行交给下游的字段是 station_id,charge_loss,discharge_loss。

调度行交给执行端的字段是 station_id,unit_id,energy_mwh。

报告收口区放 unit_count,station_count,total_loss_mwh。

assignments 要解释观测总量，不能只按输入顺序复制。

losses 是每位小数后六位的非负数。

dispatch 按 unit_id 升序排列。

对同一场景重放，report.json 的每个字节都保持一致。

重放同一场景不会改变账本条目数量。

场景字节摘要改变后，ledger.jsonl 追加一条新记录。

追加账本单行由 run_id/input_digest/status/unit_count/station_count 五段拼成。

恢复文件从 station_count 开始，后三个成员是 run_id,input_digest,report_digest。

场景缺字段、带未知字段或出现重复 JSON 键时，入口返回拒收。

标准错误承载拒收原因，input refused: 是固定的起始片段。

拒收时既有 report.json,ledger.jsonl 和 checkpoint.json 保持原字节。

dispatch_request 的总电量需要落在可分配容量以内。

交付物要能让下一班在相同输入下复核归属,损耗和逐单元调度量。
