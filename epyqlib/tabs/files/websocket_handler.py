import json
from urllib.parse import urlparse

import paho.mqtt.client as mqtt
from twisted.internet.task import LoopingCall
from typing import Callable


class WebSocketHandler():
    def __init__(self):
        self._callback: Callable[[str, dict], None] = None
        self.loopingCall: LoopingCall = None
        self.clients: list[mqtt.Client] = []

    def connect(self, response: dict, on_message: Callable[[str, dict], None]) -> None:
        """
        Makes WebSocket connection and starts looping
        :param response JSON body response from a subscription call to AppSync
        :param on_message: Callable that is called on every message. The first param is the action (created/updated/deleted)
        and the second is the file json object as a dict.
        """
        self._callback = on_message

        new_subscriptions = response['extensions']['subscription']['newSubscriptions']
        mqtt_connections = response['extensions']['subscription']['mqttConnections']

        new_connections = {}
        for [action, details] in new_subscriptions.items():
            mqtt_connection = next(c for c in mqtt_connections if details['topic'] in c['topics'])
            topic = details['topic']

            if mqtt_connection['url'] not in new_connections:
                new_connections[mqtt_connection['url']] = {
                    'connection': mqtt_connection,
                    'topics': set()
                }

            new_connections[mqtt_connection['url']]['topics'].add(topic)


        for new_connection in new_connections.values():
            client_id = new_connection['connection']['client']
            url = new_connection['connection']['url']

            urlparts = urlparse(url)

            headers = {"Host": "{0:s}".format(urlparts.netloc)}

            client = mqtt.Client(client_id=client_id, transport="websockets")
            client.on_connect = self._on_connect
            client.on_message = self._on_message
            client.on_log = self._on_log
            client.on_socket_close = self._on_socket_close

            client.ws_set_options(path="{}?{}".format(urlparts.path, urlparts.query), headers=headers)
            client.tls_set()

            client.user_data_set({'topics': new_connection['topics']})

            client.connect(urlparts.netloc, port=443)
            self.clients.append(client)

        self.loopingCall: LoopingCall = LoopingCall(self.loop)
        self.loopingCall.start(1)

    def loop(self):
        if not self.is_subscribed():
            return

        # print("Looping")
        for client in self.clients:
            client.loop_read()
            client.loop_write()
            client.loop_misc()

    def disconnect(self):
        self.loopingCall.stop()

        for client in self.clients:
            client.disconnect()
        self.clients = []

    def is_subscribed(self):
        return len(self.clients) > 0

    def _on_connect(self, client: mqtt.Client, userdata: dict, flags, rc):
        topics: set[str] = userdata['topics']
        sub_list = list(map(lambda topic: (topic, 1), topics))
        print("[Graphql Websocket] On connect. Subscribing to topics: " + json.dumps(sub_list))
        client.subscribe(sub_list)

    def _on_log(self, client, userdata, level, buf):
        print(f"[Graphql Websocket]  Log {level} {buf}")

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage):
        try:
            payload = msg.payload.decode('ascii')
            payload_json = json.loads(payload)
            print(f"[Graphql Websocket] Message received: {payload}")
        except Exception as e:
            print(f"[Graphql Websocket] Error converting payload to JSON: " + msg.payload.decode('ascii'))
            print(e)
            return


        # We *should* only get one payload, but just in case...
        try:
            for action, payload in payload_json['data'].items():
                self._callback(action, payload)
        except Exception as e:
            print(f"[Graphql Websocket] Error iterating over payload: " + json.dumps(payload_json))
            print(e)

    def _on_socket_close(self, client: mqtt.Client, userdata: dict, socket):
        print(f"Connection to topic ${userdata.get('topic')} closed.")

# Example of the format of `response`:
# {
#   "extensions": {
#     "subscription": {
#       "mqttConnections": [
#         {
#           "url": "wss://a1yyia7sgxh08y-ats.iot.us-west-2.amazonaws.com/mqtt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=ASIA5..."
#           "topics": [
#             "674475255666/hmdhwjgyuja6nfrcb65tzc42vu/associationCreated/ad2fc514666b327b3e79f4255329988b30bfec23428bd62f7db900ffce4744a7",
#             "674475255666/hmdhwjgyuja6nfrcb65tzc42vu/activityCreated/38c53ba841180e186aeaae8565e9807ddabb7becaf849cf336e24d1f23e2a12b",
#             "674475255666/hmdhwjgyuja6nfrcb65tzc42vu/fileCreated/ad2fc514666b327b3e79f4255329988b30bfec23428bd62f7db900ffce4744a7"
#           ],
#           "client": "xtsaum2eyfhaxd2scqsjnajvmq"
#         },
#         {
#           "url": "wss://a1yyia7sgxh08y-ats.iot.us-west-2.amazonaws.com/mqtt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=ASIA5..."
#           "topics": [
#             "674475255666/hmdhwjgyuja6nfrcb65tzc42vu/fileUpdated/65a5e5608f9c9ce6e418848ef29015d110c059d1d02fb103288f5a7bab394e72",
#             "674475255666/hmdhwjgyuja6nfrcb65tzc42vu/associationDeleted/65a5e5608f9c9ce6e418848ef29015d110c059d1d02fb103288f5a7bab394e72",
#             "674475255666/hmdhwjgyuja6nfrcb65tzc42vu/fileUpdated/ad2fc514666b327b3e79f4255329988b30bfec23428bd62f7db900ffce4744a7"
#           ],
#           "client": "bqaskf7ddrfrnctwj4mf7fkwpi"
#         },
#         {
#           "url": "wss://a1yyia7sgxh08y-ats.iot.us-west-2.amazonaws.com/mqtt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=ASIA5..."
#           "topics": [
#             "674475255666/hmdhwjgyuja6nfrcb65tzc42vu/fileDeleted/129baf6c447f4da79303537da82ec8e71266c21536567ded0f5d997f75e889e3",
#             "674475255666/hmdhwjgyuja6nfrcb65tzc42vu/fileDeleted/65a5e5608f9c9ce6e418848ef29015d110c059d1d02fb103288f5a7bab394e72",
#             "674475255666/hmdhwjgyuja6nfrcb65tzc42vu/associationDeleted/ad2fc514666b327b3e79f4255329988b30bfec23428bd62f7db900ffce4744a7"
#           ],
#           "client": "y6myssziyffsvg7e4yfjjngaqy"
#         },
#         {
#           "url": "wss://a1yyia7sgxh08y-ats.iot.us-west-2.amazonaws.com/mqtt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=ASIA5..."
#           "topics": [
#             "674475255666/hmdhwjgyuja6nfrcb65tzc42vu/fileUpdated/",
#             "674475255666/hmdhwjgyuja6nfrcb65tzc42vu/associationCreated/65a5e5608f9c9ce6e418848ef29015d110c059d1d02fb103288f5a7bab394e72",
#             "674475255666/hmdhwjgyuja6nfrcb65tzc42vu/fileDeleted/"
#           ],
#           "client": "cbh5lbimwje2nawp42uwmp7o2e"
#         },
#         {
#           "url": "wss://a1yyia7sgxh08y-ats.iot.us-west-2.amazonaws.com/mqtt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=ASIA5..."
#           "topics": [
#             "674475255666/hmdhwjgyuja6nfrcb65tzc42vu/fileCreated/129baf6c447f4da79303537da82ec8e71266c21536567ded0f5d997f75e889e3",
#             "674475255666/hmdhwjgyuja6nfrcb65tzc42vu/fileCreated/",
#             "674475255666/hmdhwjgyuja6nfrcb65tzc42vu/fileDeleted/ad2fc514666b327b3e79f4255329988b30bfec23428bd62f7db900ffce4744a7"
#           ],
#           "client": "ollqguz5vvfn3jmyj5tsulz7su"
#         },
#         {
#           "url": "wss://a1yyia7sgxh08y-ats.iot.us-west-2.amazonaws.com/mqtt?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=ASIA5..."
#           "topics": [
#             "674475255666/hmdhwjgyuja6nfrcb65tzc42vu/fileCreated/65a5e5608f9c9ce6e418848ef29015d110c059d1d02fb103288f5a7bab394e72",
#             "674475255666/hmdhwjgyuja6nfrcb65tzc42vu/fileUpdated/129baf6c447f4da79303537da82ec8e71266c21536567ded0f5d997f75e889e3"
#           ],
#           "client": "tt7sop2eurffzhej45d4gu6ztm"
#         }
#       ],
#       "newSubscriptions": {
#         "activityCreated": {
#           "topic": "674475255666/hmdhwjgyuja6nfrcb65tzc42vu/activityCreated/38c53ba841180e186aeaae8565e9807ddabb7becaf849cf336e24d1f23e2a12b",
#           "expireTime": 1553801051000
#         },
#         "fileUpdated": {
#           "topic": "674475255666/hmdhwjgyuja6nfrcb65tzc42vu/fileUpdated/",
#           "expireTime": 1553801051000
#         }
#       }
#     }
#   },
#   "data": {
#     "activityCreated": null,
#     "fileUpdated": null
#   }
# }