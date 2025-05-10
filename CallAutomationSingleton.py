from azure.communication.callautomation import CallAutomationClient
from azure.identity import DefaultAzureCredential
import logging
import threading

class CallAutomationSingleton:
    _instance = None
    _lock = threading.Lock()  # To make it thread-safe

    @classmethod
    def get_instance(cls, acs_connection_string=None):
        with cls._lock:
            if cls._instance is None:
                try:
                    logging.info("Creating CallAutomationClient using DefaultAzureCredential")
                    cls._instance = CallAutomationClient(
                        endpoint="https://telefon-t-bachelor2025.norway.communication.azure.com/",
                        credential=DefaultAzureCredential()
                    )
                except Exception as e:
                    logging.error(f"DefaultAzureCredential failed: {e}")
                    if acs_connection_string:
                        logging.info("Falling back to connection string")
                        cls._instance = CallAutomationClient.from_connection_string(acs_connection_string)
                    else:
                        raise ValueError("No valid credentials available to initialize CallAutomationClient")
            return cls._instance
