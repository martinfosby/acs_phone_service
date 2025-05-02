import logging
import azure.functions as func
import azure.communication.callautomation as azCall
from azure.keyvault.secrets import SecretClient
from azure.identity import DefaultAzureCredential
import time
import azure.core.exceptions as azexceptions
import http.client as http_client
http_client.HTTPConnection.debuglevel = 1
logging.getLogger("azure").setLevel(logging.DEBUG)
logging.getLogger("urllib3").setLevel(logging.DEBUG)


key_vault_name = "keyvault-t-bachelor2025"
KVUri = f"https://{key_vault_name}.vault.azure.net"
acs_secret_name = "communicationServicesBachelor"
app_secret_name= "webhookOpptakApp"
credential = DefaultAzureCredential()
client = SecretClient(vault_url=KVUri, credential=credential)

acs_retrieved_secret = client.get_secret(acs_secret_name)
app_retrieved_secret = client.get_secret(app_secret_name)

# Now you can use the API key:

acsConnectionString = acs_retrieved_secret.value

# callback_uri = app_retrieved_secret.id
# callback_uri = "http://127.0.0.1:5000/callback"
callback_uri = "https://a0de-88-92-77-94.ngrok-free.app/callback"

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

@app.event_grid_trigger(arg_name="event")
@app.function_name(name="EventGridTrigger")
def EventGridTrigger(event: func.EventGridEvent): 
    logging.info('Python EventGrid trigger processed an event')
    event_data = event.get_json()
    event_type = event.event_type

     # Handle subscription validation
    if event_type == "Microsoft.EventGrid.SubscriptionValidationEvent":
        logging.info('Subscription validation event received')
        validation_code = event_data["validationCode"]
        logging.info(f"Validating EventGrid subscription with code: {validation_code}")
        return {
            "validationResponse": validation_code
        }
    
    if event_type == "Microsoft.Communication.IncomingCall":
        logging.info('Incoming call event received')
        call_automation_client = azCall.CallAutomationClient.from_connection_string(acsConnectionString)
        logging.info(f'Created call_automation_client')
        call_connection = answerCall(event_data.get("incomingCallContext"), call_automation_client)
        logging.info(f'Event data: {event_data}')
        logging.info('Successfully answered call')
        recordCall(event_data, call_connection, call_automation_client)

def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Webhook triggered")

def answerCall(incomingCallContext, call_automation_client: azCall.CallAutomationClient):
    logging.info("Ran function answerCall")
    try:
        call_connection = call_automation_client.answer_call(incoming_call_context=incomingCallContext, callback_url=callback_uri)
        logging.info(f'Answered call with ID: {call_connection.call_connection_id}')
        return call_connection
    except Exception as e:
        logging.error(f"Error recieving call: {e}")


def wait_for_established(call_connection_client, max_retries=10, delay=1):
    for _ in range(max_retries):
        call_properties = call_connection_client.get_call_properties()
        if call_properties.call_connection_state == "connected":
            return True
        time.sleep(delay)
    return False

def audioTest(call_automation_client: azCall.CallAutomationClient, call_connection):
    try:
        # sas_token = "sp=r&st=2025-05-02T01:25:03Z&se=2025-05-31T09:25:03Z&spr=https&sv=2024-11-04&sr=c&sig=WYE2D%2BxK1BDatTP9Dsr%2FTWxTIy2YrASTB8kJY8i0nwM%3D"
        # play_source = azCall.FileSource("https://stfeilmelding001.blob.core.windows.net/audio-for-playback/Recording.wav" + "?" + sas_token)
        play_source = azCall.FileSource("https://stfeilmelding001.blob.core.windows.net/audio-for-playback/Recording.wav")
        
        logging.info(f"Found sound source from storage account: {play_source.url}")
        call_connection_client = call_automation_client.get_call_connection(call_connection_id=call_connection.call_connection_id)
        logging.info(f"Created call_connection_client: {call_connection_client}")
        if not wait_for_established(call_connection_client):
            logging.error("Call did not reach Established state in time.")
            return

        call_connection_client.play_media_to_all(
            play_source=play_source,
            operation_callback_url=callback_uri)

    except Exception as e:
        logging.error(f"Error in AudioTest-function: {e}")


def recordCall(event_data: dict, call_connection: azCall.CallConnectionProperties, call_automation_client: azCall.CallAutomationClient):
    logging.info('Started running recordCall-function')
    incomingCallContext=event_data.get("incomingCallContext")
    audioTest(call_automation_client, call_connection)  
    try:
        serverCallId = call_connection.server_call_id
        logging.info(f'Server Call ID: {serverCallId}')
        # sas_token = "sp=racwdli&st=2025-05-02T13:56:21Z&se=2025-06-01T21:56:21Z&spr=https&sv=2024-11-04&sr=c&sig=Rgat6%2F6W6zfa6eFDMT5vTf8BrN%2BFj%2BgH8IMwDi8AN4c%3D"
        # blob_container_url = "https://stfeilmelding001.blob.core.windows.net/opptaker" + "?" + sas_token
        blob_container_url = "https://stfeilmelding001.blob.core.windows.net/opptaker"

        # Start recording with direct parameter specification
        try:
            response = call_automation_client.start_recording(
                server_call_id=serverCallId,
                recording_state_callback_url=callback_uri,
                recording_content_type=azCall.RecordingContent.AUDIO,
                recording_channel_type=azCall.RecordingChannel.UNMIXED,
                recording_format_type=azCall.RecordingFormat.WAV,
                recording_storage=azCall.AzureBlobContainerRecordingStorage(container_url=blob_container_url)
            )
        except azexceptions.HttpResponseError as e:
            logging.error(f"Azure Error Code: {e.status_code}")
            logging.error(f"Azure Error Message: {e.message}")
            logging.error(f"Response JSON: {e.response.content.decode('utf-8')}")
        except Exception as e:
            import traceback
            logging.error("Exception occurred during recording.")
            logging.error(f"Error: {e}")
            logging.error(traceback.format_exc())

        logging.info(f'Recording ID: {response.recording_id}')
        # recording_properties = call_automation_client.get_recording_properties(response.recording_id)
        # recording_download_url = call_automation_client.download_recording(recording_id=response.recording_id, destination_path="output.wav")
        # max_tones_to_collect = 5
        # dtmf_recognize=call_automation_client.get_call_connection(call_connection.call_connection_id).start_recognizing_media( 
        #     dtmf_max_tones_to_collect=max_tones_to_collect, 
        #     input_type=azCall.RecognizeInputType.DTMF, 
        #     target_participant=azCall.target_participant, 
        #     initial_silence_timeout=30,  
        #     dtmf_inter_tone_timeout=5, 
        #     interrupt_prompt=True, 
        #     dtmf_stop_tones=[ azCall.DtmfTone.Pound ])
        # logging.info("Start recognizing")


    except Exception as e:
        logging.error(f"Error recording call: {e}")
