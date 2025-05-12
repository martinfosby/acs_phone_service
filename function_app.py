import json
import logging
import os
import azure.functions as func
import azure.communication.callautomation as az_call
from azure.keyvault.secrets import SecretClient
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, BlobClient, UserDelegationKey
from azure.communication.callautomation import PhoneNumberIdentifier
import time
import azure.core.exceptions as azexceptions
import requests
from azure.storage.blob import generate_blob_sas, BlobSasPermissions
from datetime import datetime, timedelta, timezone
from azure.core.exceptions import ResourceNotFoundError, HttpResponseError, ClientAuthenticationError
from azure.communication.identity import CommunicationIdentityClient
from CallAutomationSingleton import CallAutomationSingleton
from threading import Timer

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
callback_url_retrieved_secret = secret_client.get_secret(callback_secret_name)

acs_connection_string = acs_retrieved_secret.value

# callback url needs to be a public endpoint via https, e.g. Azure Function, or ngrok for local development
callback_url = os.environ.get("CALLBACK_URL") or callback_url_retrieved_secret.value # test first for env var, then for secret. Useful for local development

# Global variable to store the CallAutomationClient
call_automation_client = None

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


# from MockCommunicationIdentityClient import MockCommunicationIdentityClient
# # Usage (no Azure calls = no cost)
# client = MockCommunicationIdentityClient()
# user = client.create_user()
# token = client.get_token(user, ["voip"])
# print(f"Mock user: {user.identifier}, token: {token['token']}")


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
        call_automation_client = CallAutomationSingleton.get_instance(acs_connection_string)
        # call_automation_client = get_call_automation_client()
        logging.info(f'Created call_automation_client {call_automation_client}')
        call_connection_properties = answer_call(event_data.get("incomingCallContext"), call_automation_client)
        

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

# def get_call_automation_client() -> az_call.CallAutomationClient:
#     try:
#         logging.info(f"Creating CallAutomationClient using credentials")
#         call_automation_client = az_call.CallAutomationClient("https://telefon-t-bachelor2025.norway.communication.azure.com/", credential=credential)
#     except Exception as e:
#         logging.error(f"Error creating CallAutomationClient using credentials: {e}")
#         logging.info(f"Creating CallAutomationClient using connection string")
#         call_automation_client = az_call.CallAutomationClient.from_connection_string(acs_connection_string)
#     return call_automation_client

def get_call_automation_client() -> az_call.CallAutomationClient:
    try:
        logging.info(f"Creating CallAutomationClient using connection string")
        call_automation_client = az_call.CallAutomationClient.from_connection_string(acs_connection_string)
    except Exception as e:
        logging.error(f"Error creating CallAutomationClient using connection string: {e}")
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



def get_blob_client_from_blob_service_client(container_name: str, blob_name: str) -> BlobClient:
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

# def trigger_if_call_still_active(call_id):
#     # Check if the call is still active (e.g., in a dict or via API)
#     if call_id in call_start_times:
#         print(f"Call {call_id} is still active after 60 seconds. Triggering event.")
#         # Call your logic here: notify, emit event, etc.


def return_file_source_with_sas_token(container_name: str, blob_name: str, generate_sas: bool = True, delegation_key: bool | None = None):
    blob_client = get_blob_client_from_blob_service_client(container_name=container_name, blob_name=blob_name)
    if generate_sas and not delegation_key:
        try:
            # Try with master key
            sas_token = create_service_sas_blob(blob_client.account_name, 
                                                container_name, 
                                                blob_name, 
                                                account_key=storage_retrieved_secret.value or os.environ.get("STORAGE_ACCOUNT_KEY"))
            logging.info(f"Generated SAS token: {sas_token}")
            # Then use this URL with your az_call.FileSource
            play_source = az_call.FileSource(blob_client.url + "?" + sas_token)
            logging.info(f"Found sound source from storage account with generated SAS token: {play_source.url}")
        except Exception as e:
            # Master key fails, try with secret as fallback
            logging.error(f"Error in return_file_source_with_sas_token function: {e}")
            sas_retrieved_secret = secret_client.get_secret(sas_secret_name)
            sas_token = sas_retrieved_secret.value or os.environ.get("SAS_TOKEN")
            play_source = az_call.FileSource(blob_client.url + "?" + sas_token)
            logging.info(f"Found sound source from storage account with secret SAS token: {play_source.url}")
        return play_source
    elif generate_sas and delegation_key:
        # If generate_sas is true and delegation key is provided, use delegation key
        try:
            # Try with delegation key
            start_time = datetime.now(timezone.utc)
            expiry_time = start_time + timedelta(hours=2)
            del_key = get_delegation_key(BlobServiceClient(account_url="https://stfeilmelding001.blob.core.windows.net", credential=credential), 
                                            start_time=start_time, 
                                            expiry_time=expiry_time)

            # Generate a SAS token for the blob
            sas_token = create_service_sas_blob(blob_client.account_name, container_name, blob_name, delegation_key=del_key)
            logging.info(f"Generated SAS token: {sas_token}")
            # Then use this URL with your az_call.FileSource
            play_source = az_call.FileSource(blob_client.url + "?" + sas_token)
            logging.info(f"Found sound source from storage account with generated SAS token: {play_source.url}")
        except Exception as e:
            # If delegation key fails, try with master key
            sas_token = create_service_sas_blob(blob_client.account_name, 
                                                container_name, 
                                                blob_name, 
                                                account_key=storage_retrieved_secret.value or os.environ.get("STORAGE_ACCOUNT_KEY"))
            logging.info(f"Generated SAS token: {sas_token}")
            # Then use this URL with your az_call.FileSource
            play_source = az_call.FileSource(blob_client.url + "?" + sas_token)
            logging.info(f"Found sound source from storage account with generated SAS token: {play_source.url}")
        return play_source
    else:
        # If generate_sas is false, try with secret as fallback
        sas_retrieved_secret = secret_client.get_secret(sas_secret_name)
        sas_token = sas_retrieved_secret.value or os.environ.get("SAS_TOKEN")
        play_source = az_call.FileSource(blob_client.url + "?" + sas_token)
        logging.info(f"Found sound source from storage account with secret SAS token: {play_source.url}")
        return play_source


def audio_playback_to_all(call_connection_client: az_call.CallConnectionClient, container_name: str = "audio-for-playback", blob_name: str = "Recording.wav", generate_sas: bool = True):
    play_source = return_file_source_with_sas_token(container_name=container_name, blob_name=blob_name, generate_sas=generate_sas)
    call_connection_client.play_media_to_all(
        play_source=play_source,
        operation_callback_url=callback_url
    )

        



def get_account_key(account_name: str) -> str:
    """
    Retrieves the storage account key either from the environment or throws an error if not found.
    """
    account_key = os.environ.get("STORAGE_ACCOUNT_KEY")
    if not account_key:
        raise ValueError("No account key provided and STORAGE_ACCOUNT_KEY not set in environment")
    return account_key


def get_delegation_key(blob_service_client: BlobServiceClient, start_time: datetime, expiry_time: datetime) -> UserDelegationKey:
    """
    Retrieves the user delegation key from the Azure Blob Service.
    """
    try:
        return blob_service_client.get_user_delegation_key(
            key_start_time=start_time,
            key_expiry_time=expiry_time
        )
    except Exception as e:
        logging.error(f"Failed to retrieve user delegation key: {str(e)}")
        raise


def create_service_sas_blob(
    account_name: str,
    container_name: str,
    blob_name: str,
    delegation_key: UserDelegationKey | None = None,
    account_key: str | None = None
) -> str:
    """
    Generates a SAS token for the specified blob with fallback mechanisms.
    Tries to use user delegation first, then falls back to account key if needed.

    Args:
        account_name: Name of the storage account
        container_name: Name of the container
        blob_name: Name of the blob
        delegation_key: Optional pre-existing user delegation key
        account_key: Optional storage account key (will check environment if not provided)

    Returns:
        Generated SAS token as a string

    Raises:
        ValueError: If neither delegation key nor account key authentication succeeds
        azure.core.exceptions.ClientAuthenticationError: For Azure AD auth failures
        Exception: For other unexpected errors
    """
    start_time = datetime.now(timezone.utc)
    expiry_time = start_time + timedelta(hours=2)
    sas_token = None

    # If delegation key is available or fallback is needed to account key
    if delegation_key:
        try:
            sas_token = generate_blob_sas(
                account_name=account_name,
                container_name=container_name,
                blob_name=blob_name,
                user_delegation_key=delegation_key,
                permission=BlobSasPermissions(read=True, write=True, create=True),
                expiry=expiry_time
            )
            return sas_token
        except Exception as e:
            logging.warning(f"Failed to generate SAS token with user delegation: {str(e)}")
            if account_key is None:
                logging.info("Attempting account key fallback...")

    # Fallback to account key if user delegation failed or wasn't attempted
    try:
        if account_key is None:
            account_key = get_account_key(account_name)

        sas_token = generate_blob_sas(
            account_name=account_name,
            container_name=container_name,
            blob_name=blob_name,
            account_key=account_key,
            permission=BlobSasPermissions(read=True, write=True, create=True),
            expiry=expiry_time
        )
        return sas_token

    except ClientAuthenticationError as e:
        logging.error(f"Authentication failed: {str(e)}")
        raise
    except Exception as e:
        logging.error(f"Account key SAS generation failed: {str(e)}")
        raise ValueError("Failed to generate SAS token with both user delegation and account key methods") from e

def record_call(input_server_call_id: str, call_automation_client: az_call.CallAutomationClient):
    """
    Starts recording the call using the CallAutomationClient instance and the call connection properties.
    The recording is stored in an Azure Blob Storage container.
    Use Microsoft.Communication.RecordingFileStatusUpdated event to get the recording details, it's gets triggered when the recording is finished.
    Args:
        call_connection: The call connection properties.
        call_automation_client: The CallAutomationClient instance.

    Returns:
        None
    """
    logging.info('Started running recordCall-function')
    try:
        logging.info(f'Server Call ID: {input_server_call_id}')
        blob_container_url = "https://stfeilmelding001.blob.core.windows.net/opptaker"

        # Start recording with direct parameter specification.
        # Note: mp3 must use the mixed channel type, for wav you can use both.
        try:
            response = call_automation_client.start_recording(
                server_call_id=input_server_call_id,
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


def recognize_dtmf(call_connection_client: az_call.CallConnectionClient = None, delegation_key: bool = True):
    logging.info('Started running recognizeDtmf-function')
    try:
        max_tones_to_collect = 5
        user_phone_number = call_connection_client.get_call_properties().source.properties.get('value')
        call_connection_client.start_recognizing_media(
            input_type=az_call.RecognizeInputType.DTMF,
            target_participant=PhoneNumberIdentifier(user_phone_number),
            initial_silence_timeout=60,
            play_prompt=return_file_source_with_sas_token(container_name="audio-for-playback", blob_name="combined.wav", 
                                                          delegation_key=delegation_key),
            dtmf_max_tones_to_collect=max_tones_to_collect,
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
        tones_to_interpret = {
            "zero": 0,
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
        }

        # Hvis det er en liste, iterer over hvert event
        events = data if isinstance(data, list) else [data]

        for event in events:
            event_type = event.get("type")
            logging.info(f"Received event: {event_type} with data: {event}")

            if event_type == "Microsoft.Communication.AddParticipantSucceeded":
                logging.info('AddParticipantSucceeded event received')
                call_connection_id = event.get("data").get("callConnectionId")
                logging.info(f"Participant added to call with ID: {call_connection_id}")


            elif event_type == "Microsoft.Communication.RecognizeCompleted":
                logging.info('RecognizeCompleted event received')
                operation_context = event.get("operationContext")
                logging.info(f"Recognize operation completed with context: {operation_context}")
                result_information = event.get("data").get("resultInformation")
                logging.info(f"Result information: {result_information}")
                dtmf_result = event.get("data").get("dtmfResult")
                tones = dtmf_result.get("tones")
                logging.info(f"DTMF Tones: {tones}")
                
                
                

                call_connection_client = get_call_connection_client(event.get("data").get("callConnectionId"), CallAutomationSingleton.get_instance(acs_connection_string))
                call_properties =call_connection_client.get_call_properties()
                logging.info(f"Call properties: {call_properties}")

                # Start audio playback
                try:
                    audio_playback_to_all(call_connection_client, container_name="audio-for-playback", blob_name="denne-samtalen-blir-tatt-opp.wav")
                except Exception as e:
                    logging.error(f"Failed to start audio playback after RecognizeCompleted: {e}")
                

                # Check if the result information is from maximum number of tones
                if len(tones) == 5 and operation_context == "employee-id-recognition":
                    logging.info("Maximum number of tones received")
                    tones_interpreted = [tones_to_interpret.get(tone) for tone in tones if tone in tones_to_interpret]
                    for tone_interpreted in tones_interpreted:
                        if not isinstance(tone_interpreted, int):
                            logging.info(f"Invalid tone received: {tone_interpreted}")
                            return func.HttpResponse(status_code=400)
                    call_data = {
                        "call_connection_id": call_properties.call_connection_id,
                        "call_connection_state": call_properties.call_connection_state,
                        "callback_url": call_properties.callback_url,
                        "correlation_id": call_properties.correlation_id,
                        "server_call_id": call_properties.server_call_id,
                        "source": call_properties.source.raw_id,
                        "source_caller_id_number": call_properties.source_caller_id_number,
                        "source_display_name": call_properties.source_display_name,
                        "target": [target.raw_id for target in call_properties.targets],
                        "answered_by": call_properties.answered_by.id,
                        "answered_for": call_properties.answered_for.raw_id,
                        "tones": tones,
                        "tones_interpreted": tones_interpreted,
                    }
                    json_data = json.dumps(call_data)

                    try:
                        blob_name = f"{event.get('data').get('callConnectionId')}_{datetime.now().strftime('%Y-%m-%d')}.json"
                        blob_client = get_blob_client_from_blob_service_client("ansattnr-fra-telefon", blob_name)
                        blob_client.upload_blob(json_data, overwrite=True)
                    except ClientAuthenticationError as auth_error:
                        logging.error(f"Authentication error in RecognizeCompleted event from blob upload: {auth_error}")
                    except HttpResponseError as http_error:
                        if http_error.status_code == 403:
                            logging.error(f"Permission denied. Ensure your credentials have write access to the Blob container: {http_error}")
                            del_key = get_delegation_key(BlobServiceClient(account_url="https://stfeilmelding001.blob.core.windows.net", credential=credential), 
                                                         start_time=datetime.now(timezone.utc), 
                                                         expiry_time=datetime.now(timezone.utc) + timedelta(hours=2))
                            try:
                                # try first with delegation key
                                sas_token = create_service_sas_blob(blob_client.account_name, blob_client.container_name, blob_name, delegation_key=del_key)
                                
                                # Create new client with SAS token
                                sas_blob_client = BlobClient.from_blob_url(f"{blob_client.url}?{sas_token}")
                                sas_blob_client.upload_blob(json_data, overwrite=True)
                                logging.info(f"Uploaded blob with SAS token")
                            except Exception as e:
                                # if delegation key does not work, try with account key
                                logging.info(f"Uploaded blob with SAS token")
                                logging.error(f"Error uploading blob with SAS token: {e}")
                                sas_token = create_service_sas_blob(blob_client.account_name, blob_client.container_name, blob_name, account_key=storage_retrieved_secret.value or os.environ.get("STORAGE_ACCOUNT_KEY"))
                                
                                # Create new client with SAS token
                                sas_blob_client = BlobClient.from_blob_url(f"{blob_client.url}?{sas_token}")
                                sas_blob_client.upload_blob(json_data, overwrite=True)

                        elif http_error.status_code == 404:
                            logging.error("Blob container not found. Ensure your credentials have write access to the Blob container.")
                        else:
                            logging.error(f"HttpResponseError in RecognizeCompleted event from blob upload: {http_error}")

                    
                    except Exception as e:
                        logging.error(f"Error uploading blob in RecognizeCompleted event: {e}")

            elif event_type == "Microsoft.Communication.RecognizeFailed":
                logging.info('RecognizeFailed event received')
                logging.warning(f"Warning RecognizeFailed: {event.get('data').get('resultInformation')}")
                try:
                    # Try to recognize DTMF without delegation key
                    recognize_dtmf(
                        call_connection_client=get_call_connection_client(
                            event.get("data").get("callConnectionId"), 
                            CallAutomationSingleton.get_instance(acs_connection_string)), 
                        delegation_key=False)
                except Exception as e:
                    logging.error(f"Error in RecognizeFailed event: {e}")

            elif event_type == "Microsoft.Communication.RecognizeCanceled":
                logging.info('RecognizeCanceled event received')
            
            elif event_type == "Microsoft.Communication.SendDtmfCompleted":
                logging.info('SendDtmfCompleted event received')

            elif event_type == "Microsoft.Communication.CallConnected":
                # Safely get the call connection ID
                call_connection_id = event.get("data").get("callConnectionId")
                logging.info(f"Call is now connected. ID: {call_connection_id}")

                try:
                    # Get call automation singleton instance
                    call_automation_client = CallAutomationSingleton.get_instance(acs_connection_string)
                    logging.info(f'Created call_automation_client after PlayCompleted: {call_automation_client}')
                except Exception as e:
                    logging.error(f"Failed to create CallAutomationClient after PlayCompleted: {e}")

                # Create a new CallConnectionClient
                try:
                    call_connection_client = get_call_connection_client(call_connection_id=call_connection_id, call_automation_client=call_automation_client)
                    logging.info(f"Call connection client created: {call_connection_client}")
                except Exception as e:
                    logging.error(f"Failed to create CallConnectionClient after CallConnected: {e}")

                # Start audio playback
                # try:
                #     audio_playback_to_all(call_connection_client, container_name="audio-for-playback", blob_name="combined.wav")
                # except Exception as e:
                #     logging.error(f"Failed to start audio playback after CallConnected: {e}")
                
                # # Start continuous DTMF recognition
                # try:
                #     start_continous_dtmf_recognition(call_connection_client)
                # except Exception as e:
                #     logging.error(f"Failed to start continuous DTMF recognition after CallConnected: {e}")

                # Start DTMF recognition
                try:
                    recognize_dtmf(call_connection_client)
                except Exception as e:
                    logging.error(f"Failed to start DTMF recognition after CallConnected: {e}")

            elif event_type == "Microsoft.Communication.Play":
                logging.info('Play event received')
                operation_context = event.get("operationContext")
                logging.info(f"Play operation started with context: {operation_context}")

            elif event_type == "Microsoft.Communication.PlayCompleted":
                logging.info('PlayCompleted event received')
                operation_context = event.get("operationContext")
                logging.info(f"Play operation completed with context: {operation_context}")
                
                try:
                    # Get call automation singleton instance
                    call_automation_client = CallAutomationSingleton.get_instance(acs_connection_string)
                    logging.info(f'Created call_automation_client after PlayCompleted: {call_automation_client}')
                except Exception as e:
                    logging.error(f"Failed to create CallAutomationClient after PlayCompleted: {e}")

                try:
                    logging.info(f"Running record call")
                    # Start recording
                    record_call(event.get("data").get("serverCallId"), call_automation_client)
                except Exception as e:
                    logging.error(f"Failed to start recording after CallConnected: {e}")
            
            # elif event_type == "Microsoft.Communication.ContinuousDtmfRecognitionToneReceived":
            #     try:
            #         logging.info('ContinuousDtmfRecognitionToneReceived event received')
            #         logging.info(f"DTMF data: {event.get("data")}")

            #         # process tones here
            #         dtmf_tones.append(event.get("data").get("tone"))
            #     except Exception as e:
            #         logging.error(f"Error processing ContinuousDtmfRecognitionToneReceived event: {e}")
                
            # elif event_type == "Microsoft.Communication.ContinuousDtmfRecognitionStopped":
            #     try:
            #         logging.info('ContinuousDtmfRecognitionStopped event received')
            #         logging.info(f"DTMF tones: {dtmf_tones}")
            #         blob_service_client = get_blob_service_client("ansattnr-fra-telefon", event.get("data").get("callConnectionId") + f"{datetime.now().strftime('%Y-%m-%d')}.txt")
            #         blob_client = blob_service_client.get_blob_client()
            #         blob_client.upload_blob(
            #             data=dtmf_tones,
            #             blob_type="AppendBlob",
            #             overwrite=True
            #         )
            #     except Exception as e:
            #         logging.error(f"Error processing ContinuousDtmfRecognitionStopped event: {e}")

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

