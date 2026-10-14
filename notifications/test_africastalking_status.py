import httpx

from notifications.africastalking import sms_accepted


def _resp(body):
    return httpx.Response(201, json=body)


def test_sent_recipient_is_accepted():
    assert sms_accepted(_resp({"SMSMessageData": {"Recipients": [{"statusCode": 101, "status": "Success"}]}}))


def test_rejected_recipient_is_failure():
    assert not sms_accepted(
        _resp({"SMSMessageData": {"Recipients": [{"statusCode": 405, "status": "InsufficientBalance"}]}})
    )
    assert not sms_accepted(_resp({"SMSMessageData": {"Message": "InvalidSenderId", "Recipients": []}}))
