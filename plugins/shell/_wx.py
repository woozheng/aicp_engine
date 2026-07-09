"""GUI 对话框封装 — 消息框、输入框、选择框"""
import asyncio
import wx


async def execute(envelop, agent):
    payload = envelop.payload.get("payload", envelop.payload)
    action = payload.get("action", "")

    # ========== 框选区域 ==========
    if action == "start_selection":
        title = payload.get("title", "框选区域")
        fullscreen = payload.get("fullscreen", True)
        
        # 直接调用 system.start_selection（它已经处理了线程问题）
        result = await agent.system.start_selection(title, fullscreen)
        envelop.payload = result
        return envelop

    # ========== 显示消息框 ==========
    elif action == "show_message":
        msg = payload.get("message", "")
        title = payload.get("title", "提示")
        style = payload.get("style", "info")
        
        future = asyncio.Future()
        
        def _show():
            if style == "error":
                wx.MessageBox(msg, title, wx.OK | wx.ICON_ERROR)
            elif style == "warning":
                wx.MessageBox(msg, title, wx.OK | wx.ICON_WARNING)
            elif style == "question":
                dlg = wx.MessageDialog(None, msg, title, wx.YES_NO | wx.ICON_QUESTION)
                result = dlg.ShowModal()
                dlg.Destroy()
                future.set_result(result == wx.ID_YES)
                return
            else:
                wx.MessageBox(msg, title, wx.OK | wx.ICON_INFORMATION)
            future.set_result(True)
        
        wx.CallAfter(_show)
        result = await future
        envelop.payload = {"ok": result}
        return envelop

    # ========== 输入对话框 ==========
    elif action == "show_input_dialog":
        prompt = payload.get("prompt", "")
        title = payload.get("title", "输入")
        default = payload.get("default", "")
        
        future = asyncio.Future()
        
        def _show():
            dlg = wx.TextEntryDialog(None, prompt, title, defaultValue=default)
            if dlg.ShowModal() == wx.ID_OK:
                future.set_result(dlg.GetValue())
            else:
                future.set_result(None)
            dlg.Destroy()
        
        wx.CallAfter(_show)
        result = await future
        envelop.payload = {"value": result}
        return envelop

    # ========== 选择对话框 ==========
    elif action == "show_choice_dialog":
        message = payload.get("message", "")
        title = payload.get("title", "选择")
        choices = payload.get("choices", [])
        
        future = asyncio.Future()
        
        def _show():
            dlg = wx.SingleChoiceDialog(None, message, title, choices)
            if dlg.ShowModal() == wx.ID_OK:
                future.set_result(dlg.GetStringSelection())
            else:
                future.set_result(None)
            dlg.Destroy()
        
        wx.CallAfter(_show)
        result = await future
        envelop.payload = {"value": result}
        return envelop

    envelop.payload = {"error": f"Unknown action: {action}"}
    return envelop