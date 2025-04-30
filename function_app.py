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

callback_uri=app_retrieved_secret

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

@app.function_name(name="eventGridTrigger")
@app.event_grid_trigger(arg_name="event")
def eventGridTest(event: func.EventGridEvent): 
    logging.info('Python EventGrid trigger processed an event')
    logging.info('Successfully reacted to call')
    event_data=event.get_json()
    # if event_data.get("eventType") == "Microsoft.Communication.IncomingCall":
    call_connection=answerCall(event_data.get("incomingCallContext"))
    logging.info(f'{event_data}')
    logging.info(f'{call_connection.answered_by}')
    # logging.info('Successfully answered call')
    # recordCall(event_data, call_connection)

def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("Webhook triggered")

def answerCall(incomingCallContext):
    logging.info("Ran function answerCall")
    try:
        call_automation_client = azCall.CallAutomationClient.from_connection_string(acsConnectionString)
        call_connection=call_automation_client.answer_call(incoming_call_context=incomingCallContext, callback_url=callback_uri)
        logging.info(f'Answered call with ID: {call_connection.source_caller_id_number}')
        return call_connection
    except Exception as e:
        logging.error(f"Error recieving call.")
