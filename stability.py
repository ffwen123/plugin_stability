#!/usr/bin/env python
# -*- coding=utf-8 -*-
"""
@time: 2023/5/23 16:46
@Project ：chatgpt-on-wechat
@file: stability.py
"""
import json
import os
import unicodedata
import requests
import base64
from io import BytesIO
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from bridge.bridge import Bridge
from config import conf
import plugins
from plugins import *
from common.log import logger
from common.expired_dict import ExpiredDict


def is_chinese(prompt):
    for char in prompt:
        if char in ["\r", "\t", "\n"]:
            continue
        if "CJK" in unicodedata.name(char):
            return True
    return False


@plugins.register(name="Stability", desc="用stability api来画图", desire_priority=1, version="0.1", author="ffwen123")
class Stability(Plugin):
    def __init__(self):
        super().__init__()
        curdir = os.path.dirname(__file__)
        config_path = os.path.join(curdir, "config.json")
        self.params_cache = ExpiredDict(60 * 60)
        if not os.path.exists(config_path):
            logger.info('[RP] 配置文件不存在，将使用config.json.template模板')
            config_path = os.path.join(curdir, "config.json.template")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                self.api_url = config["api_url"]
                self.text_engine_id = config["text_engine_id"]
                self.image_engine_id = config["image_engine_id"]
                self.rule = config["rule"]
                self.headers = config["headers"]
                self.default_params = config["defaults"]
                self.default_parameters = config["default_parameters"]
                self.image_parameters = config["image_parameters"]
                self.st_api_key = self.headers.get("Authorization", "")
                if not self.st_api_key or "你的API 密钥" in self.st_api_key:
                    raise Exception("please set your Stability api key in config or environment variable.")
            self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
            logger.info("[RP] inited")
        except Exception as e:
            if isinstance(e, FileNotFoundError):
                logger.warn(f"[RP] init failed, config.json not found.")
            else:
                logger.warn("[RP] init failed." + str(e))
            raise e

    def on_handle_context(self, e_context: EventContext):
        if e_context['context'].type not in [ContextType.IMAGE_CREATE, ContextType.IMAGE]:
            return
        logger.info("[RP] image_query={}".format(e_context['context'].content))
        reply = Reply()
        try:
            user_id = e_context['context']["session_id"]
            content = e_context['context'].content[:]
            if e_context['context'].type == ContextType.IMAGE_CREATE:
                # 解析用户输入 如"mj [img2img] prompt1 0.5;"
                text = content
                if "help" in text or "帮助" in text:
                    reply.type = ReplyType.INFO
                    reply.content = self.get_help_text(verbose=True)
                else:
                    flag = False
                    if self.rule.get("image") in text:
                        flag = True
                        text = text.replace(self.rule.get("image"), "")
                    if is_chinese(text):
                        text = Bridge().fetch_translate(text, to_lang="en")
                    params = {**self.default_params}
                    if params.get("text", ""):
                        params["text"] += f", {text}"
                    else:
                        params["text"] += f"{text}"
                    logger.info("[RP] params={}".format(params))
                    if flag:
                        self.params_cache[user_id] = params
                        reply.type = ReplyType.INFO
                        reply.content = "请发送一张图片给我"
                    else:
                        post_json = {**{"text_prompts": [params]}, **self.default_parameters}
                        text_header = {**self.headers, "Content-Type": "application/json"}
                        logger.info("[RP] txt2img post_json={}".format(post_json))
                        # 调用stability api来画图
                        text_response = requests.post(
                            url=self.api_url.format(self.text_engine_id, "text-to-image"), json=post_json,
                            headers=text_header, timeout=300.05)
                        if text_response.status_code == 200:
                            reply.type = ReplyType.IMAGE
                            reply.content = BytesIO(base64.b64decode(text_response.json()["artifacts"][0]["base64"]))
                        else:
                            reply.type = ReplyType.ERROR
                            reply.content = "画图失败"
                            e_context['reply'] = reply
                            logger.error("[RP] Stability  API api_data: %s " % text_response.text)
                    e_context.action = EventAction.BREAK_PASS  # 事件结束后，跳过处理context的默认逻辑
                    e_context['reply'] = reply
            else:
                cmsg = e_context['context']['msg']
                if user_id in self.params_cache:
                    params = self.params_cache[user_id]
                    del self.params_cache[user_id]
                    cmsg.prepare()
                    img_data = open(content, "rb")
                    img_post = {**self.default_parameters, **self.image_parameters}
                    img_post.update({"text_prompts[0][text]": params["text"]})
                    img_post.pop("height", "")
                    img_post.pop("width", "")
                    logger.info("[RP] img2img post_json={}".format(img_post))
                    # 调用Stability api图生图
                    img_response = requests.post(
                        url=self.api_url.format(self.image_engine_id, "image-to-image"), data=img_post,
                        files={"init_image": img_data}, headers=self.headers, timeout=300.05)
                    if img_response.status_code == 200:
                        reply.type = ReplyType.IMAGE
                        reply.content = BytesIO(base64.b64decode(img_response.json()["artifacts"][0]["base64"]))
                    else:
                        reply.type = ReplyType.ERROR
                        reply.content = "img2img 画图失败"
                        e_context['reply'] = reply
                        logger.error(f"[RP] Stability API api_data: {img_response.text}, status_code: {img_response.status_code}")
                    e_context['reply'] = reply
                    e_context.action = EventAction.BREAK_PASS  # 事件结束后，跳过处理context的默认逻辑
        except Exception as e:
            reply.type = ReplyType.ERROR
            reply.content = "[RP] " + str(e)
            e_context['reply'] = reply
            logger.exception("[RP] exception: %s" % e)
            e_context.action = EventAction.CONTINUE

    def get_help_text(self, verbose=False, **kwargs):
        if not conf().get('image_create_prefix'):
            return "画图功能未启用"
        else:
            trigger = conf()['image_create_prefix'][0]
        help_text = "利用stability api来画图。\n"
        if not verbose:
            return help_text
        help_text += f"使用方法:\n使用\"{trigger}[关键词1] [关键词2]...:提示语\"的格式作画，如\"{trigger}二次元:girl\"\n"
        return help_text
