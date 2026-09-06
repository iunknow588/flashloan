from __future__ import annotations


def inject_workflow_context_markup(html: str) -> str:
    if "workflow-context.js" in html:
        return html
    script_src = '<script src="/static/workflow-context.js"></script>'
    init_script = (
        '<script>window.WorkflowContext && window.WorkflowContext.init()'
        '.catch(function(error){console.error("workflow context:", error);});</script>'
    )
    if "</head>" in html:
        html = html.replace("</head>", script_src + "</head>", 1)
    elif "</body>" in html:
        html = html.replace("</body>", script_src + "</body>", 1)
    if "</body>" in html:
        html = html.replace("</body>", init_script + "</body>", 1)
    return html
