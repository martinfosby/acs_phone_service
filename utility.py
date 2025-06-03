import json
import logging
import os
import azure.communication.callautomation as az_call
from azure.storage.blob import BlobServiceClient, BlobClient, UserDelegationKey
import time
import azure.core.exceptions as azexceptions
from azure.storage.blob import generate_blob_sas, BlobSasPermissions
from datetime import datetime, timedelta, timezone
from azure.core.exceptions import ResourceNotFoundError, HttpResponseError, ClientAuthenticationError
from azure.communication.identity import CommunicationUserIdentifier, PhoneNumberIdentifier
from CallAutomationSingleton import CallAutomationSingleton
from azure.communication.callautomation import CallConnectionProperties
import asyncio

from config import *
from CallAutomationSingleton import CallAutomationSingleton


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
        credential=default_credential if os.getenv("CLOUD_ENV") == "azure" else named_key_credential,
    )

    # Get a reference to the container
    container_client = blob_service_client.get_container_client(container_name)

    # Get a reference to the blob
    blob_client = container_client.get_blob_client(blob_name)
    return blob_client




def stop_recording_after_delay(recording_id, delay_seconds=180):
    if isinstance(delay_seconds, str):
        try:
            delay_seconds = float(delay_seconds)
        except ValueError:
            logging.error(f"Invalid delay_seconds value: {delay_seconds}. Must be an float.")
            return
    try:
        logging.info(f"Stopping recording: {recording_id} after {delay_seconds} seconds.")
        time.sleep(delay_seconds)
    except Exception as e:
        logging.error(f"Error during sleep in stop_recording_after_delay: {e}")
        return
    
    try:
        CallAutomationSingleton.get_instance().stop_recording(recording_id)
        logging.info(f"Recording: {recording_id} stopped after {delay_seconds} seconds.")
    except Exception as e:
        logging.error(f"Failed to stop recording: {e}")


def stop_call_after_delay(connection_id, delay_seconds=600):
    if isinstance(delay_seconds, str):
        try:
            delay_seconds = float(delay_seconds)
        except ValueError:
            logging.error(f"Invalid delay_seconds value: {delay_seconds}. Must be an float.")
            return
    try:
        logging.info(f"Stopping call: {connection_id} after {delay_seconds} seconds.")
        time.sleep(delay_seconds)
    except Exception as e:
        logging.error(f"Error during sleep in stop_call_after_delay: {e}")
        return
    try:
        CallAutomationSingleton.get_call_connection_client(connection_id).hang_up(is_for_everyone=True)
        logging.info(f"Call: {connection_id} stopped after {delay_seconds} seconds.")
    except Exception as e:
        logging.error(f"Failed to stop call: {e}")





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
            del_key = get_delegation_key(BlobServiceClient(account_url="https://stfeilmelding001.blob.core.windows.net", credential=default_credential), 
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
    




def audio_playback_to_all(call_connection_client: az_call.CallConnectionClient, 
                          operation_context: str, 
                          loop: bool = False,
                          interrupt_call_media_operation: bool = True,
                          callback_url: str = callback_url, 
                          container_name: str = "audio-for-playback", 
                          blob_name: str = "instruksjoner.wav", 
                          generate_sas: bool = True):
    play_source = return_file_source_with_sas_token(container_name=container_name, blob_name=blob_name, generate_sas=generate_sas)
    call_connection_client.play_media_to_all(
        play_source=play_source,
        operation_callback_url=callback_url,
        operation_context=operation_context,
        loop=loop,
        interrupt_call_media_operation=interrupt_call_media_operation
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

def recognize_dtmf(operation_context: str, 
                   play_audio: bool = True, 
                   play_back_audio_file: str = "instruksjoner.wav", 
                   call_connection_client: az_call.CallConnectionClient = None, 
                   delegation_key: bool = True):
    logging.info('Started running recognizeDtmf-function')
    try:
        max_tones_to_collect = 5
        call_properties = call_connection_client.get_call_properties()
        match call_properties.source.kind:
            case az_call.CommunicationIdentifierKind.PHONE_NUMBER:
                # call_connection_client.cancel_all_media_operations()
                user_phone_number = PhoneNumberIdentifier(call_properties.source.properties.get("value"))
                call_connection_client.start_recognizing_media(
                    input_type=az_call.RecognizeInputType.DTMF,
                    target_participant=user_phone_number,
                    initial_silence_timeout=60,
                    play_prompt=return_file_source_with_sas_token(container_name="audio-for-playback", blob_name=play_back_audio_file, 
                                                                delegation_key=delegation_key) if play_audio else None,
                    dtmf_max_tones_to_collect=max_tones_to_collect,
                    dtmf_inter_tone_timeout=10,
                    interrupt_prompt=True,
                    operation_context=operation_context,
                    dtmf_stop_tones=[az_call.DtmfTone.POUND]
                )
            case az_call.CommunicationIdentifierKind.COMMUNICATION_USER:
                # call_connection_client.cancel_all_media_operations()
                user_client = CommunicationUserIdentifier(call_properties.source.raw_id)
                call_connection_client.start_recognizing_media(
                    input_type=az_call.RecognizeInputType.DTMF,
                    target_participant=user_client,
                    initial_silence_timeout=60,
                    play_prompt=return_file_source_with_sas_token(container_name="audio-for-playback", blob_name=play_back_audio_file, 
                                                                delegation_key=delegation_key) if play_audio else None,
                    dtmf_max_tones_to_collect=max_tones_to_collect,
                    dtmf_inter_tone_timeout=10,
                    interrupt_prompt=True,
                    operation_context=operation_context,
                    dtmf_stop_tones=[az_call.DtmfTone.POUND]
                )


        
        logging.info("Started recognizing DTMF tones")
    except azexceptions.HttpResponseError as e:
        logging.error(f"HTTP error during DTMF recognition: {e.message}")
    except Exception as e:
        logging.error(f"Unexpected error in recognize_dtmf-function: {e}")

def start_continous_dtmf_recognition(
        call_connection_client: az_call.CallConnectionClient,
        operation_context: str):
    logging.info('Started running start_continous_dtmf_recognition-function')
    try:
        user_raw_id = call_connection_client.get_call_properties().source.raw_id
        user_kind = call_connection_client.get_call_properties().source.kind
        match user_kind:
            case az_call.CommunicationIdentifierKind.PHONE_NUMBER:
                call_connection_client.start_continuous_dtmf_recognition(
                    target_participant=PhoneNumberIdentifier(call_connection_client.get_call_properties().source.properties.get("value")),
                    operation_context=operation_context,
                )
            case az_call.CommunicationIdentifierKind.COMMUNICATION_USER:
                call_connection_client.start_continuous_dtmf_recognition(
                    target_participant=CommunicationUserIdentifier(user_raw_id),
                    operation_context=operation_context,
                )
        logging.info("Started continuous DTMF recognition")
    except azexceptions.HttpResponseError as e:
        logging.error(f"HTTP error during starting continuous DTMF recognition: {e.message}")
    except Exception as e:
        logging.error(f"Unexpected error in start_continous_dtmf_recognition-function: {e}")


def stop_continous_dtmf_recognition(
        call_connection_client: az_call.CallConnectionClient,
        operation_context: str):
    logging.info('Started running stop_continous_dtmf_recognition-function')
    try:
        user_raw_id = call_connection_client.get_call_properties().source.raw_id
        user_kind = call_connection_client.get_call_properties().source.kind
        match user_kind:
            case az_call.CommunicationIdentifierKind.PHONE_NUMBER:
                call_connection_client.stop_continuous_dtmf_recognition(
                    target_participant=PhoneNumberIdentifier(call_connection_client.get_call_properties().source.properties.get("value")),
                    operation_context=operation_context,
                )
            case az_call.CommunicationIdentifierKind.COMMUNICATION_USER:
                call_connection_client.stop_continuous_dtmf_recognition(
                    target_participant=CommunicationUserIdentifier(user_raw_id),
                    operation_context=operation_context,
                )
        logging.info("Stopped continuous DTMF recognition")
    except azexceptions.HttpResponseError as e:
        logging.error(f"HTTP error during stopping continuous DTMF recognition: {e.message}")
    except Exception as e:
        logging.error(f"Unexpected error in stop_continous_dtmf_recognition-function: {e}")



def interpret_dtmf(tones, call_properties: CallConnectionProperties):
    tones_to_interpret = {
        "zero": "0",
        "one": "1",
        "two": "2",
        "three": "3",
        "four": "4",
        "five": "5",
        "six": "6",
        "seven": "7",
        "eight": "8",
        "nine": "9",
    }

    # Check if the result information is from maximum number of tones
    if len(tones) == 5:
        logging.info("Maximum number of tones received")
        tones_interpreted = [tones_to_interpret.get(tone) for tone in tones if tone in tones_to_interpret]
        for tone_interpreted in tones_interpreted:
            try:
                int(tone_interpreted)
            except ValueError:
                logging.info(f"Invalid tone received: {tone_interpreted}")
                raise ValueError(f"Invalid tone received: {tone_interpreted}")

        call_data = {
            "call_connection_id": call_properties.call_connection_id,
            "call_connection_state": call_properties.call_connection_state,
            "callback_url": call_properties.callback_url,
            "correlation_id": call_properties.correlation_id,
            "server_call_id": call_properties.server_call_id,
            "source": call_properties.source.raw_id,
            "source_caller_id_number": call_properties.source_caller_id_number,
            "source_display_name": call_properties.source_display_name,

            # Serialize lists to JSON strings
            "target": json.dumps([target.raw_id for target in call_properties.targets]),
            "tones": json.dumps(tones),

            "answered_by": call_properties.answered_by.raw_id if call_properties.answered_by is not None else None,
            "answered_for": call_properties.answered_for.raw_id if call_properties.answered_for is not None else None,
            
            "tones_interpreted": "".join(tones_interpreted),
        }


        return call_data

def upload_interpret_dtmf(call_data: dict, call_properties: CallConnectionProperties):
        json_data = json.dumps(call_data)

        try:
            blob_name = f"{call_properties.source.raw_id}_{datetime.now().strftime('%Y-%m-%d')}.json"
            blob_client = get_blob_client_from_blob_service_client("ansattnr-fra-telefon", blob_name)
            blob_client.upload_blob(json_data, overwrite=True)
        except ClientAuthenticationError as auth_error:
            logging.error(f"Authentication error in RecognizeCompleted event from blob upload: {auth_error}")
        except HttpResponseError as http_error:
            if http_error.status_code == 403:
                logging.error(f"Permission denied. Ensure your credentials have write access to the Blob container: {http_error}")
                del_key = get_delegation_key(BlobServiceClient(account_url="https://stfeilmelding001.blob.core.windows.net", credential=default_credential), 
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


def cleanup_call(running_tasks, call_connection_id):
    tasks = running_tasks.get(call_connection_id)
    if tasks:
        for task in tasks:
            try:
                asyncio.get_event_loop().run_until_complete(task)
            except asyncio.CancelledError:
                logging.info(f"Task for call {call_connection_id} cancelled successfully")
            del running_tasks[call_connection_id]
