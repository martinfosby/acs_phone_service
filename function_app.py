import logging
import azure.functions as func
import azure.communication.callautomation as azCall
from azure.keyvault.secrets import SecretClient
from azure.identity import DefaultAzureCredential


key_vault_name = "keyvault-t-bachelor2025"
KVUri = f"https://{key_vault_name}.vault.azure.net"
acs_secret_name = "communicationServicesBachelor"
app_secret_name= "webhookOpptakApp"
credential = DefaultAzureCredential()
client = SecretClient(vault_url=KVUri, credential=credential)

acs_retrieved_secret = client.get_secret(acs_secret_name)
app_retrieved_secret=client.get_secret(app_secret_name)

# Now you can use the API key:

acsConnectionString = acs_retrieved_secret.value

callback_uri=app_retrieved_secret.value

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

@app.function_name(name="eventGridTrigger")
@app.event_grid_trigger(arg_name="event")
def eventGridTest(event: func.EventGridEvent): 
    logging.info('Python EventGrid trigger processed an event')
    logging.info('Successfully reacted to call')
    event_data=event.get_json()
    # if event_data.get("eventType") == "Microsoft.Communication.IncomingCall":
    call_automation_client = azCall.CallAutomationClient.from_connection_string(acsConnectionString)
    logging.info(f'Created call_automation_client')
    call_connection=answerCall(event_data.get("incomingCallContext"), call_automation_client)
    logging.info(f'Event data: {event_data}')
    logging.info('Successfully answered call')
    audioTest(call_automation_client, call_connection=call_connection)
    # recordCall(event_data, call_connection)

def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Webhook triggered")

def answerCall(incomingCallContext, call_automation_client):
    logging.info("Ran function answerCall")
    try:
        call_connection=call_automation_client.answer_call(incoming_call_context=incomingCallContext, callback_url=callback_uri)
        logging.info(f'Answered call with ID: {call_connection.source_caller_id_number}')
        return call_connection
    except Exception as e:
        logging.error(f"Error recieving call.")

def audioTest(call_automation_client, call_connection):
    try:
        play_source = azCall.FileSource("https://stfeilmelding001.blob.core.windows.net/audio-for-playback/Recording.wav")
        logging.info(f"Found sound source from storage account: {play_source}")
        call_connection_client=call_automation_client.get_call_connection(call_connection_id=call_connection.call_connection_id)
        logging.info(f"Created call_connection_client: {call_connection_client}")
        call_connection_client.play_media_to_all(
            play_source=play_source,
            operation_callback_url=callback_uri)

    except Exception as e:
        logging.error("Error in AudioTest-function")

def recordCall(event_data, call_connection):
    logging.info('Started running recordCall-function')
    incomingCallContext=event_data.get("incomingCallContext")
    try:
        call_automation_client = azCall.CallAutomationClient.from_connection_string(acsConnectionString)
        # call_connection=call_automation_client.answer_call(incoming_call_context=incomingCallContext, callback_url=callback_uri)
        serverCallId=call_connection.server_call_id
        logging.info(f'Server Call ID: {serverCallId}')
        # response = call_automation_client.start_recording(server_call_id=serverCallId, 
        #     recording_state_callback_url=callback_uri,
        #     recording_content_type = azCall.RecordingContent.Audio,
        #     recording_channel_type = azCall.RecordingChannel.Unmixed,
        #     recording_format_type = azCall.RecordingFormat.Mp3,
            
        #     # recording_storage = azCall.AzureBlobContainerRecordingStorage(container_url="https://stfeilmelding001.blob.core.windows.net/opptaker")
        #     )
        # logging.info(f'Recording ID: {response.recording_id}')
        play_source = azCall.FileSource("https://stfeilmelding001.blob.core.windows.net/audio-for-playback/Recording.wav")
        call_automation_client.get_call_connection(call_connection_id=call_connection.call_connection_id).play_media_to_all(
            play_source=play_source
        )
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
        logging.error(f"Error recieving call.")