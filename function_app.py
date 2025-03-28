import logging
import azure.functions as func
import azure.communication.callautomation as azCall
from azure.keyvault.secrets import SecretClient
from azure.identity import DefaultAzureCredential

key_vault_name = "keyvault-t-bachelor2025"
KVUri = f"https://{key_vault_name}.vault.azure.net"
acs_secret_name = "communicationServicesBachelor"
credential = DefaultAzureCredential()
client = SecretClient(vault_url=KVUri, credential=credential)

acs_retrieved_secret = client.get_secret(acs_secret_name)

# Now you can use the API key:

acsConnectionString = acs_retrieved_secret.value

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

@app.event_grid_trigger(arg_name="azeventgrid")
def EventGridTriggerInboundCall(azeventgrid: func.EventGridEvent):
    if func.EventGridEvent.event_type == "Microsoft.Communication.CallStarted":
        logging.info('Python EventGrid trigger processed an event')
        main()


def main():
    try:
        call_automation_client = azCall.CallAutomationClient.from_connection_string(acsConnectionString)
        response = call_automation_client.start_recording(call_locator=azCall.ServerCallLocator(azCall.server_call_id),
            recording_content_type = azCall.RecordingContent.AUDIO,
            recording_channel_type = azCall.RecordingChannel.UNMIXED,
            recording_format_type = azCall.RecordingFormat.MP3,
            recording_storage = azCall.AzureBlobContainerRecordingStorage(container_url="https://stfeilmelding001.blob.core.windows.net/opptaker"))
        
        # max_tones_to_collect = 5
        # call_automation_client.get_call_connection(azCall.call_connection_id).start_recognizing_media( 
        #     dtmf_max_tones_to_collect=max_tones_to_collect, 
        #     input_type=azCall.RecognizeInputType.DTMF, 
        #     target_participant=azCall.target_participant, 
        #     initial_silence_timeout=30,  
        #     dtmf_inter_tone_timeout=5, 
        #     interrupt_prompt=True, 
        #     choices=choices,
        #     dtmf_stop_tones=[ azCall.DtmfTone.Pound ])
        # logging.info("Start recognizing")

    except Exception as e:
        logging.error(f"Error recieving call.")