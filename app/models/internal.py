from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

ParseMode = Literal["Markdown", "MarkdownV2", "HTML"]


class TelegramSendMessageRequest(BaseModel):
    chat_id: int | str
    text: str = Field(min_length=1, max_length=4096)
    parse_mode: ParseMode | None = None
    disable_notification: bool | None = None
    protect_content: bool | None = None
    message_thread_id: int | None = Field(default=None, ge=1)
    reply_to_message_id: int | None = Field(default=None, ge=1)
    allow_sending_without_reply: bool | None = None
    reply_markup: dict[str, Any] | None = None


class TelegramSendPhotoRequest(BaseModel):
    chat_id: int | str
    caption: str | None = Field(default=None, max_length=1024)
    parse_mode: ParseMode | None = None
    disable_notification: bool | None = None
    protect_content: bool | None = None
    message_thread_id: int | None = Field(default=None, ge=1)
    reply_to_message_id: int | None = Field(default=None, ge=1)
    allow_sending_without_reply: bool | None = None
    reply_markup: dict[str, Any] | None = None


class TelegramEditMessageTextRequest(BaseModel):
    chat_id: int | str | None = None
    message_id: int | None = Field(default=None, ge=1)
    inline_message_id: str | None = None
    text: str = Field(min_length=1, max_length=4096)
    parse_mode: ParseMode | None = None
    disable_web_page_preview: bool | None = None
    reply_markup: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_message_target(self) -> "TelegramEditMessageTextRequest":
        if self.inline_message_id is not None:
            return self
        if self.chat_id is not None and self.message_id is not None:
            return self
        raise ValueError(
            "either inline_message_id or both chat_id and message_id are required"
        )


class TelegramEditMessageCaptionRequest(BaseModel):
    chat_id: int | str | None = None
    message_id: int | None = Field(default=None, ge=1)
    inline_message_id: str | None = None
    caption: str = Field(min_length=1, max_length=1024)
    parse_mode: ParseMode | None = None
    reply_markup: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_message_target(self) -> "TelegramEditMessageCaptionRequest":
        if self.inline_message_id is not None:
            return self
        if self.chat_id is not None and self.message_id is not None:
            return self
        raise ValueError(
            "either inline_message_id or both chat_id and message_id are required"
        )


class TelegramAnswerCallbackQueryRequest(BaseModel):
    callback_query_id: str = Field(min_length=1)
    text: str | None = Field(default=None, max_length=200)
    show_alert: bool | None = None
    url: str | None = None
    cache_time: int | None = Field(default=None, ge=0)


class TelegramDeleteMessageRequest(BaseModel):
    chat_id: int | str
    message_id: int = Field(ge=1)


ChatAction = Literal[
    "typing",
    "upload_photo",
    "record_video",
    "upload_video",
    "record_voice",
    "upload_voice",
    "upload_document",
    "choose_sticker",
    "find_location",
    "record_video_note",
    "upload_video_note",
]


class TelegramSendChatActionRequest(BaseModel):
    chat_id: int | str
    action: ChatAction
    message_thread_id: int | None = Field(default=None, ge=1)


class TelegramMethodSuccessResponse(BaseModel):
    ok: Literal[True]
    result: dict[str, Any] | bool


class TelegramMethodErrorResponse(BaseModel):
    ok: Literal[False]
    error_type: Literal[
        "validation_error",
        "auth_error",
        "operation_not_allowed",
        "relay_timeout",
        "relay_network_error",
        "telegram_http_error",
        "telegram_api_error",
    ]
    message: str
    status_code: int
    telegram_status_code: int | None = None
    telegram_error_code: int | None = None
    telegram_description: str | None = None
    telegram_response: dict[str, Any] | None = None
    telegram_response_text: str | None = None
    details: list[dict[str, Any]] | None = None
