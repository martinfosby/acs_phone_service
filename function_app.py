import logging
import os
import azure.functions as func
import azure.communication.callautomation as az_call
from azure.keyvault.secrets import SecretClient
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
import time
import azure.core.exceptions as azexceptions
import requests
from azure.storage.blob import generate_blob_sas, BlobSasPermissions
from datetime import datetime, timedelta, timezone

start_time = datetime.now(timezone.utc)
expiry_time = start_time + timedelta(days=1)

# import http.client as http_client
# http_client.HTTPConnection.debuglevel = 1
# logging.getLogger("azure").setLevel(logging.DEBUG)
# logging.getLogger("urllib3").setLevel(logging.DEBUG)


key_vault_name = "keyvault-t-bachelor2025"
kv_uri = f"https://{key_vault_name}.vault.azure.net"
acs_secret_name = "communicationServicesBachelor"
app_secret_name= "webhookOpptakApp"
sas_secret_name = "sas-token-acs"
storage_secret_name = "stfeilmelding001-account-key"
credential = DefaultAzureCredential()
secret_client = SecretClient(vault_url=kv_uri, credential=credential)

acs_retrieved_secret = secret_client.get_secret(acs_secret_name)
app_retrieved_secret = secret_client.get_secret(app_secret_name)
sas_retrieved_secret = secret_client.get_secret(sas_secret_name)
storage_retrieved_secret = secret_client.get_secret(storage_secret_name)
# Now you can use the API key:

acs_connection_string = acs_retrieved_secret.value

# callback_url = app_retrieved_secret.value
# callback_uri = "http://127.0.0.1:5000/callback"
# callback_uri = "https://a0de-88-92-77-94.ngrok-free.app/callback"
callback_url = "https://opptak-t-bachelor2025.azurewebsites.net/api/callback"

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

@app.event_grid_trigger(arg_name="event")
@app.function_name(name="PhoneRecordEventGridTrigger")
def phone_record_event_grid_trigger(event: func.EventGridEvent): 
    logging.info('Python EventGrid trigger processed an event')
    event_data = event.get_json()
    logging.info(f'Event data: {event_data}')
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
        global call_automation_client
        call_automation_client = az_call.CallAutomationClient.from_connection_string(acs_connection_string)
        logging.info(f'Created call_automation_client')
        call_connection = answer_call(event_data.get("incomingCallContext"), call_automation_client)
        logging.info('Successfully answered call')
        call_connection_client = get_call_connection_client(call_automation_client, call_connection)
        logging.info(f"Running audio playback")
        audio_playback(call_connection_client)
        logging.info(f"Running record call")
        record_call(call_connection, call_automation_client)

    if event_type == "Microsoft.Communication.RecordingFileStatusUpdated":
        logging.info('Recording file status updated event received')
        requests.post("http://127.0.0.1:5000/webhook", json=event_data)
        
        # recording_file_status = event_data.get("recordingFileStatus")
        # recording_url = event_data.get("recordingUrl")
        # logging.info(f"Recording file status: {recording_file_status}")
        # logging.info(f"Recording URL: {recording_url}")




def answer_call(incomingCallContext, call_automation_client: az_call.CallAutomationClient):
    logging.info("Ran function answerCall")
    try:
        call_connection = call_automation_client.answer_call(incoming_call_context=incomingCallContext, callback_url=callback_url)
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

def get_blob_service_client():
    # Connect to your storage account
    blob_service_client = BlobServiceClient(
        account_url="https://stfeilmelding001.blob.core.windows.net",
        credential=credential
    )

    # Get a reference to the container
    container_client = blob_service_client.get_container_client("audio-for-playback")

    # Get a reference to the blob
    blob_client = container_client.get_blob_client("Recording.wav")
    return blob_client

def get_call_connection_client(call_automation_client: az_call.CallAutomationClient, call_connection: az_call.CallConnectionProperties):
    try:
        call_connection_client = call_automation_client.get_call_connection(call_connection_id=call_connection.call_connection_id)
        logging.info(f"Created call_connection_client: {call_connection_client}")
        if not wait_for_established(call_connection_client):
            logging.error("Call did not reach Established state in time.")
            return
        logging.info("Call is established.")
        return call_connection_client
    except Exception as e:
        logging.error(f"Error creating call connection client: {e}")
        raise


def audio_playback(call_connection_client: az_call.CallConnectionClient):
    try:
        blob_client = get_blob_service_client()
        logging.info(f"Blob client created: {blob_client}")        
        # Get the blob URL - no SAS token needed now
        blob_url = blob_client.url

        # Generate a SAS token for the blob
        sas_token = create_service_sas_blob(blob_client, storage_retrieved_secret.value or os.environ.get("STORAGE_ACCOUNT_KEY"))
        logging.info(f"Generated SAS token: {sas_token}")
        # Then use this URL with your az_call.FileSource
        play_source = az_call.FileSource(blob_url + "?" + sas_token)
        logging.info(f"Found sound source from storage account: {play_source.url}")
        
        logging.info(f"Playing media to all participants")
        call_connection_client.play_media_to_all(
            play_source=play_source,
            operation_callback_url=callback_url)

    except Exception as e:
        logging.error(f"Error in AudioTest-function: {e}")
        sas_token = sas_retrieved_secret.value or os.environ.get("SAS_TOKEN")
        play_source = az_call.FileSource("https://stfeilmelding001.blob.core.windows.net/audio-for-playback/Recording.wav" + "?" + sas_token)

        logging.info(f"Playing media to all participants with SAS token")
        call_connection_client.play_media_to_all(
            play_source=play_source,
            operation_callback_url=callback_url)
        
def create_service_sas_blob(blob_service_client: BlobServiceClient, account_key: str):
    """
    Generates a read-only SAS token for the specified blob, valid for 24 hours.
    """
    # Create a SAS token that's valid for one day, as an example
    start_time = datetime.now(timezone.utc)
    expiry_time = start_time + timedelta(minutes=5)

    sas_token = generate_blob_sas(
        account_name=blob_service_client.account_name,
        container_name=blob_service_client.container_name,
        blob_name=blob_service_client.blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=expiry_time,
        start=start_time
    )

    return sas_token

def record_call(call_connection: az_call.CallConnectionProperties, call_automation_client: az_call.CallAutomationClient):
    logging.info('Started running recordCall-function')
    audio_playback(call_automation_client, call_connection)  
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
                recording_state_callback_url=callback_url,
                recording_content_type=az_call.RecordingContent.AUDIO,
                recording_channel_type=az_call.RecordingChannel.MIXED,
                recording_format_type=az_call.RecordingFormat.MP3,
                recording_storage=az_call.AzureBlobContainerRecordingStorage(container_url=blob_container_url)
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


    except Exception as e:
        logging.error(f"Error recording call: {e}")

def recognizeDtmf(call_automation_client: az_call.CallAutomationClient, call_connection: az_call.CallConnectionProperties):
    logging.info('Started running recognizeDtmf-function')
    try:
        # Start recognizing DTMF tones
        max_tones_to_collect = 5
        dtmf_recognize = call_automation_client.get_call_connection(call_connection.call_connection_id).start_recognizing_media( 
            input_type=az_call.RecognizeInputType.DTMF, 
            target_participant=az_call.CallParticipant(), 
            dtmf_max_tones_to_collect=max_tones_to_collect, 
            initial_silence_timeout=30,  
            dtmf_inter_tone_timeout=5, 
            interrupt_prompt=True, 
            dtmf_stop_tones=[ az_call.DtmfTone.Pound ])
        logging.info("Start recognizing")
    except Exception as e:
        logging.error(f"Error in recognizeDtmf-function: {e}")


@app.route(route="callback", auth_level=func.AuthLevel.ANONYMOUS)
@app.function_name(name="Callback")
def callback(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('ACS callback triggered.')
    try:
        data = req.get_json()

        # Hvis det er en liste, iterer over hvert event
        events = data if isinstance(data, list) else [data]

        for event in events:
            event_type = event.get("type")
            logging.info(f"Received event: {event_type} with data: {event}")

            if event_type == "Microsoft.Communication.RecognizeCompleted":
                logging.info('RecognizeCompleted event received')
                tones = event.get("recognitionResult", {}).get("tones")
                logging.info(f"DTMF Tones: {tones}")
                # process tones here

            elif event_type == "Microsoft.Communication.RecordingStateChanged":
                logging.info('RecordingStateChanged event received')
                state = event.get("data").get("state")
                logging.info(f"Recording state changed to: {state}")
                # if state == "inactive":
                    # recording is finished

                # process recording URL here

            elif event_type == "Microsoft.Communication.PlayFailed":
                logging.info('PlayFailed event received')
                result_information = event.get("resultInformation")
                logging.info(f"Play failed with result: {result_information}")
                # handle play failure here

            elif event_type == "Microsoft.Communication.CallDisconnected":
                logging.info('CallDisconnected event received')
                call_id = event.get("callConnectionId")
                logging.info(f"Call disconnected with ID: {call_id}")
                # handle call disconnection here

        return func.HttpResponse("Events received", status_code=200)

    except Exception as e:
        logging.error(f"Error: {e}")
        return func.HttpResponse("Error processing event", status_code=500)
