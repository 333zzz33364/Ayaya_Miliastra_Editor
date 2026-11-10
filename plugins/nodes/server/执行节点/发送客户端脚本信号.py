from __future__ import annotations

from engine.nodes.node_spec import node_spec
from plugins.nodes.shared.server_执行节点_impl_helpers import *  # noqa: F401,F403
from engine.utils.logging.logger import log_info


@node_spec(
    name="发送客户端脚本信号",
    category="执行节点",
    semantic_id="signal.send_client",
    inputs=[
        ("流程入", "流程"),
        ("目标玩家", "实体"),
        ("信号名", "字符串"),
    ],
    outputs=[("流程出", "流程")],
    dynamic_port_type="泛型",
    description="从服务器节点图向指定玩家的客户端脚本发送信号；信号参数通过动态端口传入。",
    doc_reference="服务器节点/执行节点/执行节点.md",
)
def 发送客户端脚本信号(game, 目标玩家, 信号名: str, **kwargs):
    """从服务器节点图向指定玩家的客户端脚本发送信号。

    说明：
    - 本地 MockRuntime 只记录调用，不真正投递到客户端；
    - 真实投递由导出的官方节点 300005 在千星运行时完成；
    - 信号参数通过动态端口以关键字参数传入。
    """
    signal_name = str(信号名 or "").strip()
    if not signal_name:
        raise ValueError("信号名不能为空")

    log_info(
        "[发送客户端脚本信号] 目标玩家={} 信号={} 参数={}",
        getattr(目标玩家, "name", 目标玩家),
        signal_name,
        dict(kwargs),
    )
    return None
