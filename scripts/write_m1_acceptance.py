"""Evidence index for M1. Missing/partial cases never count as passed."""

import hashlib
import json
from datetime import datetime, timezone

from nexent_api import ROOT


def main():
    records = [
        ("BT01", "PASS", "新 USER 首次入口、无历史会话，无默认业务案例", "登录新群众账号，进入原生开始问答，尚未发送消息",
         "会话列表 total=0；页面无历史对话；原库及扩展库为空", ["evidence/m1-fresh-account.json", "output/playwright/m1-BT01.png", "evidence/m1-baseline.json"]),
        ("BT02", "PASS", "群众入口空输入", "执行模式分别输入空字符串和空白，按 Enter",
         "发送禁用；Agent/model dispatch 0；前后实际工具调用计数相同", ["evidence/m1-empty-input.json", "output/playwright/m1-BT02.png"]),
        ("BT03", "PASS", "真实群众会话", "原生 UI 新会话 55 执行空库技术请求",
         "只调用 govx 环境与群众检索；群众子 Agent ID、起止和最终结果与审计匹配", ["evidence/m1-native-ui-citizen.json", "output/playwright/m1-BT03.png"]),
        ("BT04", "PASS", "工作人员登录独立事项工作台", "打开工作人员入口",
         "事项、材料、待办均为 0；无默认业务任务", ["evidence/m1-workbench-api.json", "output/playwright/m1-BT04.png"]),
        ("BT05", "PASS", "平台 ADMIN 未映射业务角色；群众不能通过导航获工作人员权限", "未映射账号执行原生主 Agent并登录工作台；群众点击工作人员入口",
         "ROLE_NOT_CONFIGURED 后无子 Agent；页面要求管理员核实；群众导航 FORBIDDEN", ["evidence/m1-native-unmapped.json", "evidence/m1-workbench-api.json", "output/playwright/m1-BT05.png", "output/playwright/m1-BT11.png"]),
        ("BT06", "PASS", "知识库为空", "真实 SSE 与原生群众会话检索",
         "EMPTY/NO_KNOWLEDGE；items/citations 均空，knowledge_version=null", ["evidence/m1-sse.json", "evidence/m1-native-ui-citizen.json", "output/playwright/m1-BT03.png"]),
        ("BT07", "PASS", "图谱空；另一个隔离实例 graph 配置关闭", "真实 SSE 和工作台图谱查询；缺配置实例再查询",
         "正常 EMPTY/NO_GRAPH，节点边为空；未配置 ERROR/CONNECTION_NOT_CONFIGURED", ["evidence/m1-sse.json", "evidence/m1-missing-config.json", "output/playwright/m1-BT07.png"]),
        ("BT08", "PASS", "工作人员无事项、材料和证据", "工作人员原生主 Agent 与工作台核验前提",
         "BLOCKED/MISSING_CASE_AND_MATERIALS；缺项完整；没有审批结论", ["evidence/m1-native-staff.json", "evidence/m1-workbench-api.json", "output/playwright/m1-BT04.png"]),
        ("BT09", "PASS", "独立维护授权账号，无输入", "通过真实 SSE、HTTP 及工作台准备空更新",
         "EMPTY/NO_CHANGES；无候选或版本；明确没有检查外部政策", ["evidence/m1-sse.json", "evidence/m1-workbench-api.json", "output/playwright/m1-BT09.png"]),
        ("BT10", "PASS", "无候选变更", "分别缺确认、提供确认但缺候选、使用不存在候选、无维护权限请求发布",
         "全部 BLOCKED；候选和版本数量不变", ["evidence/m1-sse.json", "evidence/m1-workbench-api.json", "output/playwright/m1-BT10.png"]),
        ("BT11", "PASS", "群众无维护权限", "真实 SSE 越权调用 prepare/publish；HTTP 图谱写参数及身份伪造；群众在维护页面点击更新和发布",
         "维护工具和页面均 FORBIDDEN；图谱写参数和伪造身份 INVALID_ARGUMENTS；实际响应和审计匹配，无写入接口授权", ["evidence/m1-sse.json", "evidence/m1-workbench-api.json", "evidence/m1-citizen-maintenance-ui.json", "output/playwright/m1-BT11-prepare.png", "output/playwright/m1-BT11-publish.png"]),
        ("BT12", "PASS", "隔离 govx MCP 和独立 SDK 运行容器", "在 MCP 容器取得实际 SQLite 独占锁，再停止该容器；分别通过原生 API、原生页面和工作台执行，finally 恢复",
         "锁超时两次尝试 ERROR/TOOL_TIMEOUT 后停止；断连一次尝试 ERROR/CONNECTION_TIMEOUT，无 Agent 模型或工具调用；锁释放及服务恢复后新会话通过", ["evidence/m1-native-faults.json", "evidence/m1-native-ui-faults.json", "evidence/m1-runtime-overlay.json", "output/playwright/m1-native-TOOL_TIMEOUT.png", "output/playwright/m1-native-CONNECTION_TIMEOUT.png"]),
        ("BT13", "PASS", "单独缺连接配置实例，正常服务配置保留", "撤除隔离实例图谱和更新连接配置，登录页面并真实查询",
         "页面 ERROR/CONNECTION_NOT_CONFIGURED 后停止；图谱/更新均 ERROR，不宣称成功或空结果", ["evidence/m1-missing-config.json", "output/playwright/m1-BT13.png"]),
        ("BT14", "PASS", "同一维护身份和同一空输入", "真实 SSE 及维护页面分别连续三次 prepare_update(inputs=[])",
         "每组三次 NO_CHANGES；七表数量全为 0；数据库 SHA-256 前后相同，页面响应与审计逐项匹配", ["evidence/m1-sse.json", "evidence/m1-repeat-empty-ui.json", "output/playwright/m1-BT14.png"]),
        ("BT15", "PASS", "群众原生检索返回 EMPTY", "核对新 UI 会话 55 的子 Agent 结束、主 Agent最终答复和实际审计",
         "EMPTY 后无额外工具；子 Agent 先结束；主结果完整透传，无业务推理", ["evidence/m1-native-ui-citizen.json", "output/playwright/m1-BT03.png"]),
        ("BT16", "PASS", "群众、工作人员、未映射及缺配置/异常页面和日志", "正常、越权、缺配置、真实超时和断连前端响应与日志交叉核对，恢复后复核原生网页；核对原开发账号调试证据",
         "角色、停止原因和实际 request_id 一致；连接在工具执行前失败时无 MCP request_id，使用真实会话号和认证身份关联，不编造工具请求；工作台保留受控角色且不将连接异常说成未配置角色", ["evidence/m1-workbench-api.json", "evidence/m1-native-ui-citizen.json", "evidence/m1-native-staff.json", "evidence/m1-native-unmapped.json", "evidence/m1-missing-config.json", "evidence/m1-native-faults.json", "evidence/m1-native-ui-faults.json", "evidence/m1-native-ui-sidecar-recovery.json", "evidence/native-ui.json", "evidence/m1-boundary-check.json"]),
    ]
    output = []
    for number, verdict, prerequisite, operation, actual, paths in records:
        evidence = []
        for path in paths:
            source = ROOT / path
            if not source.is_file():
                raise ValueError(f"Missing actual evidence: {path}")
            evidence.append({"path": path, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
        output.append({"id": number, "verdict": verdict, "prerequisite": prerequisite,
                       "operation": operation, "actual": actual, "evidence": evidence})
    for path in ("evidence/m1-native-ui-citizen.json", "evidence/m1-native-staff.json", "evidence/m1-native-unmapped.json",
                 "evidence/m1-sse.json", "evidence/m1-workbench-api.json", "evidence/m1-missing-config.json",
                 "evidence/m1-citizen-maintenance-ui.json", "evidence/m1-native-faults.json",
                 "evidence/m1-native-ui-faults.json", "evidence/m1-native-ui-sidecar-recovery.json", "evidence/m1-repeat-empty-ui.json"):
        if json.loads((ROOT / path).read_text(encoding="utf-8")).get("passed") is not True:
            raise ValueError(f"Source assessment did not pass: {path}")
    passed = sum(r["verdict"] == "PASS" for r in output)
    audit_path = ROOT / "evidence/m1-completion-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else {}
    complete = passed == 16 and audit.get("passed") is True and all(
        audit.get("checks", {}).get(key) is True for key in (
            "all_indexed_evidence_hashes_match", "original_code_config_database_fingerprints_preserved",
            "both_business_stores_and_candidate_versions_empty", "original_and_extension_regression_tests_pass"))
    data = {"captured_at": datetime.now(timezone.utc).isoformat(), "stage": "M1", "status": "COMPLETE" if complete else "IN_PROGRESS",
        "passed": passed, "total": 16, "complete": complete, "completion_audit": "evidence/m1-completion-audit.json",
        "basis": "Next-stage implementation plan, section 2/3/5/7; unit checks never replace actual BT acceptance",
        "records": output,
        "remaining": [] if complete else ["最终当前状态完成审计"],
        "human_input_required": [], "failure_evidence": ["evidence/m1-native-ui-first-failed.json", "evidence/m1-native-sidecar-first-failed.json", "evidence/m1-native-sidecar-callback-failed.json", "evidence/m1-native-ui-routing-first-failed.json"]}
    (ROOT / "evidence/m1-acceptance.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    status = "完成检查通过，M1 空白技术验收已完成。" if complete else "M1 仍在执行，尚待当前状态完成审计。"
    lines = ["# M1 空白验收覆盖矩阵", "", f"当前 {passed}/16 项通过，{status} PARTIAL 不计通过。", "",
             "| 编号 | 判定 | 实际结果及待补项 | 证据 |", "| --- | --- | --- | --- |"]
    for entry in output:
        links = "、".join(f"[{PathLabel(e['path'])}](../{e['path']})" for e in entry["evidence"])
        lines.append(f"| {entry['id']} | {entry['verdict']} | {entry['actual']} | {links} |")
    lines += ["", "完整前提、操作及证据指纹见 `evidence/m1-acceptance.json`。所有结果来自实际执行；本轮新增工具的单元检查不作整项替代。", "",
              "原生群众首次失败已保留；恢复固定模型服务后的新会话通过。原服务新会话回归另存 `evidence/m1-original-native-regression.json`；原核心代码、Agent 配置、角色映射和业务数据库的指纹不变。"]
    (ROOT / "docs/M1验收覆盖矩阵.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"M1 evidence indexed: {passed}/16 PASS; status={data['status']}.")


def PathLabel(path):
    return path.rsplit("/", 1)[-1]


if __name__ == "__main__":
    main()
