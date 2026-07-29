import os

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def direct_conversation_group_name(conversation_id):
    return f'direct_chat_{conversation_id}'


def serialize_direct_message(message):
    """Turn a DirectMessage into the JSON-friendly shape sent down the
    websocket (and returned by the HTTP attachment endpoint), so both paths
    render identically on the client."""
    return {
        'id': message.id,
        'sender_id': message.sender_id,
        'sender_name': str(message.sender),
        'content': message.content,
        'created_at': message.created_at.isoformat(),
        'attachments': [
            {
                'id': attachment.id,
                'url': attachment.file.url,
                'name': attachment.original_filename or os.path.basename(attachment.file.name),
                'kind': attachment.kind,
                'size': attachment.size,
            }
            for attachment in message.attachments.all()
        ],
    }


def broadcast_direct_message(message):
    """Push a freshly-created DirectMessage to anyone with the conversation
    open. Used both for attachments (created through a plain HTTP view) and
    could be reused anywhere else a message gets created outside the
    consumer itself."""
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    async_to_sync(channel_layer.group_send)(
        direct_conversation_group_name(message.conversation_id),
        {'type': 'chat_message', 'message': serialize_direct_message(message)},
    )
