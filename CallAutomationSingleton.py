from typing import Optional
import azure.communication.callautomation as az_call
from azure.communication.callautomation import CallAutomationClient, RecordingProperties
from azure.core.exceptions import HttpResponseError
from azure.identity import DefaultAzureCredential
import logging
import threading

class CallAutomationSingleton:
    _instance = None
    _lock = threading.Lock()  # To make it thread-safe

    @classmethod
    def get_instance(cls, acs_connection_string=None, aio=False):
        with cls._lock:
            if cls._instance is None:
                # try:
                #     logging.info("Creating CallAutomationClient using DefaultAzureCredential")
                #     cls._instance = CallAutomationClient(
                #         endpoint="https://telefon-t-bachelor2025.norway.communication.azure.com/",
                #         credential=DefaultAzureCredential()
                #     )
                # except Exception as e:
                #     logging.error(f"DefaultAzureCredential failed: {e}")
                #     if acs_connection_string:
                #         logging.info("Falling back to connection string")
                #         cls._instance = CallAutomationClient.from_connection_string(acs_connection_string)
                #     else:
                #         raise ValueError("No valid credentials available to initialize CallAutomationClient")
                if acs_connection_string:
                    logging.info("Falling back to connection string")
                    cls._instance = CallAutomationClient.from_connection_string(acs_connection_string)
                else:
                    raise ValueError("No valid credentials available to initialize CallAutomationClient")
            return cls._instance
        
        
    @classmethod
    def get_call_connection_client(cls, connection_id):
        with cls._lock:
            return cls._instance.get_call_connection(connection_id)
        
    @classmethod
    def answer_call(cls, incoming_call_context, callback_url, cognitive_service_endpoint=None, operation_context=None) -> az_call.CallConnectionProperties | None:
        logging.info("Ran function answerCall")
        try:
            call_connection_properties = cls.get_instance().answer_call(
                incoming_call_context=incoming_call_context, 
                callback_url=callback_url,
                cognitive_service_endpoint=cognitive_service_endpoint,
                operation_context=operation_context
            )
            logging.info(f'Answered call with ID: {call_connection_properties.call_connection_id}')
            return call_connection_properties
        except Exception as e:
            logging.error(f"Error answering call: {e}")

        
    @classmethod
    def record_call(cls, input_server_call_id: str, pause_on_start: bool, callback_url: str, blob_container_url = "https://stfeilmelding001.blob.core.windows.net/opptaker") -> Optional[RecordingProperties]:
        logging.info('Started running record_call function')
        try:
            logging.info(f'Server Call ID: {input_server_call_id}')
            
            call_automation_client = cls.get_instance()

            # Mixed Audio Recording: kr0.0204/minute
            # Mixed Audio and Video Recording: kr0.1018/minute
            # Unmixed Audio Recording: kr0.0123/minute/participant
            # Unmixed audio is cheaper than mixed audio recording.
            # Note: MP3 only supports mixed audio recording, not unmixed audio recording.
            response = call_automation_client.start_recording(
                server_call_id=input_server_call_id,
                recording_state_callback_url=callback_url,
                recording_content_type=az_call.RecordingContent.AUDIO,
                recording_channel_type=az_call.RecordingChannel.UNMIXED,
                recording_format_type=az_call.RecordingFormat.WAV,
                recording_storage=az_call.AzureBlobContainerRecordingStorage(container_url=blob_container_url),
                pause_on_start=pause_on_start
            )
            cls.recording_properties = response
            logging.info(f'Recording ID: {response.recording_id}')
            return response

        except HttpResponseError as e:
            logging.error(f"Azure Error Code: {e.status_code}")
            logging.error(f"Azure Error Message: {e.message}")
            logging.error(f"Response JSON: {e.response.content.decode('utf-8')}")
        except Exception as e:
            import traceback
            logging.error("Exception occurred during recording.")
            logging.error(f"Error: {e}")
            logging.error(traceback.format_exc())
