from dotenv import load_dotenv
import os
from azure.keyvault.secrets import SecretClient
from azure.identity import DefaultAzureCredential
from azure.core.credentials import AzureNamedKeyCredential
from datetime import datetime, timedelta, timezone

# Load environment variables from .env file (if it exists)
load_dotenv()

start_time = datetime.now(timezone.utc)
expiry_time = start_time + timedelta(days=1)

# Key Vault setup (only used as fallback)
key_vault_name = os.getenv("KEY_VAULT_NAME")
kv_uri = f"https://{key_vault_name}.vault.azure.net"
default_credential = DefaultAzureCredential()
secret_client = SecretClient(vault_url=kv_uri, credential=default_credential)

# Environment variables (preferred for local development)
acs_connection_string = os.getenv("ACS_CONNECTION_STRING")
app_secret = os.getenv("APP_SECRET")
callback_url = os.getenv("CALLBACK_URL")
storage_account_name = os.getenv("STORAGE_ACCOUNT_NAME")
storage_account_key = os.getenv("STORAGE_ACCOUNT_KEY")


# Azure Named Key Credential for Storage
named_key_credential = AzureNamedKeyCredential(
    storage_account_name,
    storage_account_key
)