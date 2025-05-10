import logging
import os
import azure.functions as func
import azure.communication.callautomation as az_call
from azure.keyvault.secrets import SecretClient
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from azure.communication.callautomation import PhoneNumberIdentifier
import time
import azure.core.exceptions as azexceptions
import requests
from azure.storage.blob import generate_blob_sas, BlobSasPermissions
from datetime import datetime, timedelta, timezone
from azure.core.exceptions import ResourceNotFoundError

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
callback_secret_name = "callback-url-fa-http-trigger"
sas_secret_name = "sas-token-acs"
storage_secret_name = "stfeilmelding001-account-key"
credential = DefaultAzureCredential()
secret_client = SecretClient(vault_url=kv_uri, credential=credential)

acs_retrieved_secret = secret_client.get_secret(acs_secret_name)
app_retrieved_secret = secret_client.get_secret(app_secret_name)
storage_retrieved_secret = secret_client.get_secret(storage_secret_name)
# callback_url_retrieved_secret = secret_client.get_secret(callback_secret_name)

acs_connection_string = acs_retrieved_secret.value

callback_url = os.environ.get("CALLBACK_URL")

# Global variable to store the CallAutomationClient
call_automation_client = None

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

@app.event_grid_trigger(arg_name="event")
@app.function_name(name="PhoneRecordEventGridTrigger")
def phone_record_event_grid_trigger(event: func.EventGridEvent): 
    logging.info('Python EventGrid trigger processed an event')
    logging.info(f"Event received at: {datetime.now(timezone.utc)}, event time: {event.event_time}")
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
        call_automation_client = get_call_automation_client()
        logging.info(f'Created call_automation_client {call_automation_client}')
        call_connection_properties = answer_call(event_data.get("incomingCallContext"), call_automation_client)
        logging.info(f'Caller information: {call_connection_properties.answered_for.raw_id}')
        logging.info(f'Call connection status: {call_connection_properties.call_connection_state}')
        

    if event_type == "Microsoft.Communication.RecordingFileStatusUpdated":
        logging.info('Recording file status updated event received')
        try:
            res = requests.post("http://127.0.0.1:5000/webhook", json=event_data)
            logging.info(f"Webhook response: {res.status_code}, {res.text}")
        except requests.exceptions.RequestException as e:
            logging.error(f"Error sending webhook: {e}")
        
        # recording_file_status = event_data.get("recordingFileStatus")
        # recording_url = event_data.get("recordingUrl")
        # logging.info(f"Recording file status: {recording_file_status}")
        # logging.info(f"Recording URL: {recording_url}")

def get_call_automation_client() -> az_call.CallAutomationClient:
    try:
        logging.info(f"Creating CallAutomationClient using credentials")
        call_automation_client = az_call.CallAutomationClient("https://telefon-t-bachelor2025.norway.communication.azure.com/", credential=credential)
    except Exception as e:
        logging.error(f"Error creating CallAutomationClient using credentials: {e}")
        logging.info(f"Creating CallAutomationClient using connection string")
        call_automation_client = az_call.CallAutomationClient.from_connection_string(acs_connection_string)
    return call_automation_client


def answer_call(incomingCallContext, call_automation_client: az_call.CallAutomationClient) -> az_call.CallConnectionProperties | None:
    logging.info("Ran function answerCall")
    try:
        call_connection_properties = call_automation_client.answer_call(incoming_call_context=incomingCallContext, callback_url=callback_url)
        logging.info(f'Answered call with ID: {call_connection_properties.call_connection_id}')
        return call_connection_properties
    except Exception as e:
        logging.error(f"Error recieving call: {e}")



def wait_for_established(call_connection_client: az_call.CallConnectionClient, max_retries=10, delay=1) -> bool:
    for attempt in range(max_retries):
        try:
            call_properties = call_connection_client.get_call_properties()
            if call_properties.call_connection_state.lower() == "connected":
                logging.info("Call is connected.")
                return True
            else:
                logging.info(f"Call state: {call_properties.call_connection_state}")
        except ResourceNotFoundError:
            logging.warning("Call not found yet, retrying...")
        except Exception as e:
            logging.error(f"Unexpected error while checking call state: {e}")
        time.sleep(delay)
    return False



def get_blob_service_client(container_name: str, blob_name: str) -> BlobServiceClient:
    # Connect to your storage account
    blob_service_client = BlobServiceClient(
        account_url="https://stfeilmelding001.blob.core.windows.net",
        credential=credential
    )

    # Get a reference to the container
    container_client = blob_service_client.get_container_client(container_name)

    # Get a reference to the blob
    blob_client = container_client.get_blob_client(blob_name)
    return blob_client


def get_call_connection_client(call_connection_id: str, call_automation_client: az_call.CallAutomationClient) -> az_call.CallConnectionClient:
    try:
        call_connection_client = call_automation_client.get_call_connection(call_connection_id=call_connection_id)
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
    blob_client = get_blob_service_client("audio-for-playback", "Recording.wav")
    logging.info(f"Blob client created: {blob_client}")        
    # Get the blob URL - no SAS token needed now
    blob_url = blob_client.url
    try:
        # Generate a SAS token for the blob
        sas_token = create_service_sas_blob(blob_client, storage_retrieved_secret.value or os.environ.get("STORAGE_ACCOUNT_KEY"))
        logging.info(f"Generated SAS token: {sas_token}")
        # Then use this URL with your az_call.FileSource
        play_source = az_call.FileSource(blob_url + "?" + sas_token)
        logging.info(f"Found sound source from storage account: {play_source.url}")
        
        call_connection_client.play_media_to_all(
            play_source=play_source,
            operation_callback_url=callback_url)
        logging.info(f"Playing media to all participants with 5 minute generated SAS token")

    except Exception as e:
        logging.error(f"Error in audio_playback function: {e}")
        sas_retrieved_secret = secret_client.get_secret(sas_secret_name)
        sas_token = sas_retrieved_secret.value or os.environ.get("SAS_TOKEN")
        play_source = az_call.FileSource(blob_url + "?" + sas_token)

        call_connection_client.play_media_to_all(
            play_source=play_source,
            operation_callback_url=callback_url)
        logging.info(f"Playing media to all participants with secret SAS token")
        

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
    try:
        serverCallId = call_connection.server_call_id
        logging.info(f'Server Call ID: {serverCallId}')
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


def recognize_dtmf(call_connection_client: az_call.CallConnectionClient = None):
    logging.info('Started running recognizeDtmf-function')
    try:
        max_tones_to_collect = 5
        user_phone_number = call_connection_client.get_call_properties().source.properties.get('value')
        call_connection_client.start_recognizing_media(
            input_type=az_call.RecognizeInputType.DTMF,
            target_participant=PhoneNumberIdentifier(user_phone_number),
            dtmf_max_tones_to_collect=max_tones_to_collect,
            initial_silence_timeout=1,
            dtmf_inter_tone_timeout=5,
            interrupt_prompt=True,
            operation_context="employee-id-recognition",
            dtmf_stop_tones=[az_call.DtmfTone.POUND]
        )
        
        logging.info("Started recognizing DTMF tones")
    except azexceptions.HttpResponseError as e:
        logging.error(f"HTTP error during DTMF recognition: {e.message}")
    except Exception as e:
        logging.error(f"Unexpected error in recognize_dtmf-function: {e}")

def start_continous_dtmf_recognition(call_connection_client: az_call.CallConnectionClient):
    logging.info('Started running startContinuousDtmfRecognition-function')
    try:
        user_phone_number = call_connection_client.get_call_properties().source.properties.get('value')
        call_connection_client.start_continuous_dtmf_recognition(
            target_participant=PhoneNumberIdentifier(user_phone_number),
            operation_context="employee-id-recognition",
        )
        logging.info("Started continuous DTMF recognition")
    except azexceptions.HttpResponseError as e:
        logging.error(f"HTTP error during continuous DTMF recognition: {e.message}")
    except Exception as e:
        logging.error(f"Unexpected error in start_continous_dtmf_recognition-function: {e}")


@app.route(route="callback", auth_level=func.AuthLevel.ANONYMOUS)
@app.function_name(name="Callback")
def callback(req: func.HttpRequest) -> func.HttpResponse:
    logging.info('ACS callback triggered.')
    try:
        data = req.get_json()
        dtmf_tones = []

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

            elif event_type == "Microsoft.Communication.CallConnected":
                # Safely get the call connection ID
                call_connection_id = event.get("data").get("callConnectionId")
                logging.info(f"Call is now connected. ID: {call_connection_id}")

                # Create a new CallAutomationClient
                call_automation_client = get_call_automation_client()

                # Create a new CallConnectionClient
                try:
                    call_connection_client = get_call_connection_client(call_connection_id=call_connection_id, call_automation_client=call_automation_client)
                    logging.info(f"Call connection client created: {call_connection_client}")
                except Exception as e:
                    logging.error(f"Failed to create CallConnectionClient after CallConnected: {e}")

                # Start audio playback
                try:
                    audio_playback(call_connection_client)
                except Exception as e:
                    logging.error(f"Failed to start audio playback after CallConnected: {e}")
                # # Start DTMF recognition
                # try:
                #     recognize_dtmf(call_connection_client)
                # except Exception as e:
                #     logging.error(f"Failed to start DTMF recognition after CallConnected: {e}")

                # Start continuous DTMF recognition
                try:
                    start_continous_dtmf_recognition(call_connection_client)
                except Exception as e:
                    logging.error(f"Failed to start continuous DTMF recognition after CallConnected: {e}")

                try:
                    logging.info(f"Running record call")
                    # Start recording
                    record_call(call_connection_client.get_call_properties(), call_automation_client)
                except Exception as e:
                    logging.error(f"Failed to start recording after CallConnected: {e}")
            
            elif event_type == "Microsoft.Communication.ContinuousDtmfRecognitionToneReceived":
                logging.info('ContinuousDtmfRecognitionToneReceived event received')
                logging.info(f"DTMF data: {event.get("data")}")

                # process tones here
                dtmf_tones.append(event.get("data").get("tone"))
                
            elif event_type == "Microsoft.Communication.ContinuousDtmfRecognitionStopped":
                logging.info('ContinuousDtmfRecognitionStopped event received')
                logging.info(f"DTMF tones: {dtmf_tones}")
                blob_service_client = get_blob_service_client("ansattnr-fra-telefon", event.get("data").get("callConnectionId") + f"{datetime.now().strftime('%Y-%m-%d')}.txt")
                blob_client = blob_service_client.get_blob_client()
                blob_client.upload_blob(
                    data=dtmf_tones,
                    blob_type="AppendBlob",
                    overwrite=True
                )

            elif event_type == "Microsoft.Communication.RecordingStateChanged":
                try:
                    logging.info('RecordingStateChanged event received')
                    state = event.get("data").get("state")
                    logging.info(f"Recording state changed to: {state}")
                except Exception as e:
                    logging.error(f"Error processing RecordingStateChanged event: {e}")

            elif event_type == "Microsoft.Communication.PlayFailed":
                try:
                    logging.info('PlayFailed event received')
                    result_information = event.get("resultInformation")
                    logging.info(f"Play failed with result: {result_information}")
                except Exception as e:
                    logging.error(f"Error processing PlayFailed event: {e}")

            elif event_type == "Microsoft.Communication.CallDisconnected":
                try:
                    logging.info('CallDisconnected event received')
                    call_id = event.get("data").get("callConnectionId")
                    logging.info(f"Call disconnected with ID: {call_id}")


                except Exception as e:
                    logging.error(f"Error processing CallDisconnected event: {e}")

        return func.HttpResponse("Events received", status_code=200)

    except Exception as e:
        logging.error(f"Callback Error: {e}")
        return func.HttpResponse("Error processing event", status_code=500)

