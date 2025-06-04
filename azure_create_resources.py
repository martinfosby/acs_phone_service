# azure_create_resources.py
import logging
import os
from azure.identity import DefaultAzureCredential
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.authorization import AuthorizationManagementClient
from azure.mgmt.authorization.models import RoleAssignmentCreateParameters
from azure.mgmt.containerinstance import ContainerInstanceManagementClient
from azure.mgmt.containerinstance.models import (
    ContainerGroup,
    Container,
    ResourceRequests,
    ResourceRequirements,
    EnvironmentVariable,
    ContainerGroupIdentity, 
    ResourceIdentityType,
    ImageRegistryCredential,
    AzureFileVolume,
    Volume,
    VolumeMount
)



from azure.mgmt.web import WebSiteManagementClient
from azure.core.exceptions import ResourceNotFoundError
from datetime import datetime
import json
import uuid
import azure.functions as func
from config import default_credential
from pathlib import Path

def create_container_instance(recording_and_call_metadata: dict):
    try:
        # Get parameters from environment variables
        logging.info("Getting parameters from environment variables...")
        azure_subscription_id = os.getenv("AZURE_SUBSCRIPTION_ID")
        azure_resource_group = os.getenv("AZURE_ACI_RESOURCE_GROUP")
        location = os.getenv("AZURE_REGION", "norwayeast")

        # Check if required environment variables are set
        logging.info("Checking if required environment variables are set...")
        if not azure_subscription_id or not azure_resource_group:
            raise ValueError("Missing required environment variables: AZURE_SUBSCRIPTION_ID and AZURE_ACI_RESOURCE_GROUP")
        
        # Default parameters for container
        logging.info("Setting default parameters for container...")
        container_name = 'audio-transcriber'
        container_group_name = f"aci-transcribe-{recording_and_call_metadata['source']['raw_id']}-{datetime.now().strftime('%Y%m%d%H%M%S')}-t-bachelor2025"
        container_image = 'transkribering.azurecr.io/nb-whisper-container:latest'
        cpu = 1.0
        memory = 8
        # Environment variables to pass to the container
        # Regular environment variables (non-sensitive)
        environment_variables = {
            "CLOUD_ENV": "local",
            "APP_ENV": "production",
            "CONTAINER_ENV": "ACI",
            "TRANSCRIPTION_MODEL": os.getenv("TRANSCRIPTION_MODEL", "NbAiLabBeta/nb-whisper-large-verbatim"),
            "TRANSCRIPTIONS_CONTAINER_NAME": os.getenv("TRANSCRIPTIONS_CONTAINER_NAME", "transkriberinger"),
        }

        # Secure environment variables (sensitive)
        secure_environment_variables = {
            "AZURE_STORAGE_CONNECTION_STRING": os.environ["AZURE_STORAGE_CONNECTION_STRING"],
            "AZURE_STORAGE_BLOB_ACCOUNT_URL": os.environ["AZURE_STORAGE_BLOB_ACCOUNT_URL"],
            "AZURE_KEY_VAULT_URL": os.environ["AZURE_KEY_VAULT_URL"],
            "AZURE_REGION": os.environ["AZURE_REGION"],
            "AZURE_STORAGE_ACCOUNT_KEY": str(os.environ["AZURE_STORAGE_ACCOUNT_KEY"]),
            "AZURE_STORAGE_ACCOUNT_NAME": str(os.environ["AZURE_STORAGE_ACCOUNT_NAME"]),
        }

        # Combine them
        combined_environment_variables = {**environment_variables, **secure_environment_variables, **recording_and_call_metadata}

        # Create ACI client
        logging.info("Creating ACI client...")
        aci_client = ContainerInstanceManagementClient(default_credential, azure_subscription_id)
        
        # Convert environment variables to the required format
        logging.info("Converting environment variables to the required format...")
        env_vars = [
            EnvironmentVariable(name=key, value=value)
            for key, value in combined_environment_variables.items()
        ]
        
        # Create container resource requirements
        logging.info("Creating container resource requirements...")
        container_resource_requirements = ResourceRequirements(
            requests=ResourceRequests(
                memory_in_gb=memory,
                cpu=cpu
            )
        )

        # Create a command to run the processing script in the container with the metadata file as an argument
        command = f"""
            python3 main.py --use-environment-variables
            --model {os.getenv('TRANSCRIPTION_MODEL', 'NbAiLabBeta/nb-whisper-large-verbatim')}
            --transcription-output-container {os.getenv('TRANSCRIPTIONS_CONTAINER_NAME', 'transkriberinger')}
        """

        # Define the Azure File Share volume
        logging.info("Defining Azure File Share volume...")
        volume_name = "nb-whisper-volume"
        azure_file_volume = AzureFileVolume(
            share_name=os.getenv("AZURE_FILE_SHARE_NAME"),
            storage_account_name=os.getenv("AZURE_STORAGE_ACCOUNT_NAME"),
            storage_account_key=os.getenv("AZURE_STORAGE_ACCOUNT_KEY")
        )

        # Create the volume where the asr model will store its cache
        logging.info("Creating volume...")
        volume = Volume(
            name=volume_name,
            azure_file=azure_file_volume
        )

        # Define the volume mount inside the container to access the asr model cache
        logging.info("Defining volume mount...")
        volume_mount = VolumeMount(
            name=volume_name,
            mount_path="/app/.cache",  # Mount point inside the container
            read_only=False  # Set True if you want read-only access
        )

        # Define the container inside the container group where the asr model will run
        logging.info("Defining container...")
        container = Container(
            name=container_name,
            image=container_image,
            resources=container_resource_requirements,
            environment_variables=env_vars,
            volume_mounts=[volume_mount],
            command=command.split(),
        )
        
        # Define the container group
        logging.info("Defining container group...")
        container_group = ContainerGroup(
            location=location,
            containers=[container],
            os_type="Linux",
            restart_policy="Never",  # Container stops after processing
            volumes=[volume],
            identity=ContainerGroupIdentity(
                type=ResourceIdentityType.USER_ASSIGNED,  # Set to user-assigned
                user_assigned_identities={
                    os.getenv("AZURE_IDENTITY_ID"): {}  # Use the ID of the managed identity created earlier
                }
            ),
            image_registry_credentials=[
                ImageRegistryCredential(
                    server="transkribering.azurecr.io",
                    identity=os.getenv("AZURE_IDENTITY_ID")
                )
            ]
        )
        
        logging.info(f"Creating or updating container group {container_group_name} in resource group {azure_resource_group}...")
        try:
            deployment_result = aci_client.container_groups.begin_create_or_update(
                azure_resource_group,
                container_group_name,
                container_group
            )
        except ResourceNotFoundError as e:
            logging.error(f"Resource group {azure_resource_group} not found: {e}")
            create_resource_group(azure_resource_group, resource_not_found_error=e)
            
            # Retry container group creation after ensuring the resource group exists
            try:
                deployment_result = aci_client.container_groups.begin_create_or_update(
                    azure_resource_group,
                    container_group_name,
                    container_group
                )
            except Exception as retry_e:
                logging.error(f"Failed to create container group {container_group_name} after creating resource group: {retry_e}")
                return  # Exit if the retry also fails

        logging.info(f"Container group {container_group_name} created or updated successfully to process blob {myblob.name}")

        
    except Exception as e:
        logging.error(f"Error creating container instance to process blob {myblob.name}: {str(e)}")


def create_resource_group(resource_group_name = None, resource_not_found_error = None):
    try:
        resource_client: ResourceManagementClient = ResourceManagementClient(default_credential, os.environ["AZURE_SUBSCRIPTION_ID"])
        resource_group_name = os.getenv("AZURE_ACI_RESOURCE_GROUP", resource_group_name)
        location = os.getenv("AZURE_REGION", "norwayeast")

        if resource_client.resource_groups.check_existence(resource_group_name) and not resource_not_found_error:
            logging.info(f"Resource group '{resource_group_name}' already exists.")
            rg = resource_client.resource_groups.get(resource_group_name)
            # Update environment variable if it already exists
            create_or_update_environment_variables("AZURE_ACI_RESOURCE_GROUP_ID", rg.id)
            return

        # Create or update the resource group
        resource_group_params = {
            "location": location,
            "tags": {
                "Applikasjon-System": "",
                "Avdeling": "",
                "Forretningseier": "",
                "Kostnadssenter": "",
                "Kritikalitet": "",
                "Levetid": "",
                "Oppetid": "",
                "Miljø": "",
            }  # Ensure required tags are added
        }
        res = resource_client.resource_groups.create_or_update(resource_group_name, resource_group_params)

        logging.info(f"Resource group '{res.name}' created or updated successfully in {res.location}.")

        # Set environment variables after creating the resource group
        create_or_update_environment_variables("AZURE_ACI_RESOURCE_GROUP_ID", res.id)
        logging.info(f"Environment variable AZURE_ACI_RESOURCE_GROUP_ID set to {res.id}")
        return res

    except Exception as e:
        logging.error(f"Error creating resource group: {e}")


def create_identity(resource_client: ResourceManagementClient, subscription_id, resource_group_name, identity_name, location):
    try:
        # Construct the resource ID for the user-assigned identity
        identity_id = f"/subscriptions/{subscription_id}/resourceGroups/{resource_group_name}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{identity_name}"

        try:
            # Attempt to get the identity by ID
            existing_identity = resource_client.resources.get_by_id(identity_id, '2024-11-30')
            
            # If the identity exists, log the information and update the environment variables
            logging.info(f"Managed Identity '{identity_name}' already exists.")
            
            if not os.getenv("AZURE_IDENTITY_ROLES"):
                assign_roles(subscription_id, resource_group_name, existing_identity, identity_name)

            # Update environment variables
            properties = existing_identity.properties or {}
            create_or_update_environment_variables("AZURE_IDENTITY_ID", existing_identity.id)
            create_or_update_environment_variables("AZURE_IDENTITY_NAME", existing_identity.name)
            create_or_update_environment_variables("AZURE_IDENTITY_PRINCIPAL_ID", properties.get("principalId"))
            create_or_update_environment_variables("AZURE_IDENTITY_CLIENT_ID", properties.get("clientId"))
            
            logging.info(f"Environment variables set for Managed Identity: {existing_identity.id}, {existing_identity.name}")
            return

        except ResourceNotFoundError:
            # If the identity doesn't exist, create it
            logging.info(f"Managed Identity '{identity_name}' does not exist. Creating it...")
            
            identity_poller = resource_client.resources.begin_create_or_update(
                resource_group_name,
                'Microsoft.ManagedIdentity',
                '',
                'userAssignedIdentities',
                identity_name,
                '2024-11-30',
                {"location": location}  # Required location
            )

            identity_result = identity_poller.result()
            
            logging.info(f"Managed Identity '{identity_name}' created with ID: {identity_result.id}")
            
            # Update environment variables
            properties = identity_result.properties or {}
            create_or_update_environment_variables("AZURE_IDENTITY_ID", identity_result.id)
            create_or_update_environment_variables_remote("AZURE_IDENTITY_ID", identity_result.id)
            create_or_update_environment_variables("AZURE_IDENTITY_NAME", identity_result.name)
            create_or_update_environment_variables("AZURE_IDENTITY_PRINCIPAL_ID", properties.get("principalId"))
            create_or_update_environment_variables("AZURE_IDENTITY_CLIENT_ID", properties.get("clientId"))

            logging.info(f"Environment variables set for Managed Identity: {identity_result.id}, {identity_result.name}")

            # Proceed with role assignment
            assign_roles(subscription_id, resource_group_name, identity_result, identity_name)

    except Exception as e:
        logging.error(f"Error creating Managed Identity: {e}")

def assign_roles(subscription_id, resource_group_name, identity_result, identity_name):
    try:
        # Authenticate the client
        authorization_client = AuthorizationManagementClient(default_credential, subscription_id)

        # Get the Principal ID of the managed identity
        principal_id = identity_result.properties.get("principalId", None)

        if not principal_id:
            raise ValueError("Managed Identity Principal ID not found")

        # Define the resource scopes
        acr_registry_name = "transkribering"  # Replace with your ACR name
        acr_registry_id = f"/subscriptions/{subscription_id}/resourceGroups/{resource_group_name}/providers/Microsoft.ContainerRegistry/registries/{acr_registry_name}"

        storage_account_id = f"/subscriptions/{subscription_id}/resourceGroups/{os.environ['AZURE_FUNCTION_APP_RESOURCE_GROUP']}"  # Adjust if targeting a specific storage account

        # Define roles to assign
        roles_to_assign = {
            "ACRPull": acr_registry_id,
            "Azure Container Storage Contributor": storage_account_id
        }


        for role_name, scope in roles_to_assign.items():
            # Get role definition
            role_definitions = list(authorization_client.role_definitions.list(
                scope, 
                filter=f"roleName eq '{role_name}'"
            ))
            
            if not role_definitions:
                raise ValueError(f"{role_name} role definition not found")

            role_assignment_id = str(uuid.uuid4())  # Use first match

            role_definition = role_definitions[0]
            
            try:
                # Create the role assignment
                role_assignment = authorization_client.role_assignments.create(
                    scope=scope,
                    role_assignment_name=role_assignment_id,  # A valid GUID
                    parameters=RoleAssignmentCreateParameters(
                        role_definition_id=role_definition.id,
                        principal_id=principal_id,
                        principal_type="ServicePrincipal"  # Managed Identity is treated as a Service Principal
                    )
                )
                logging.info(f"Assigned {role_name} role to Managed Identity {identity_name} at scope '{scope}'")
                create_or_update_environment_variables(f"AZURE_IDENTITY_{role_name.upper()}_ROLE_ID", role_assignment.id)
            except Exception as e:
                if "RoleAssignmentExists" in str(e):  # Catch specific error
                    logging.warning(f"{role_name} role already exists for {identity_name} at '{scope}', skipping.")
                else:
                    raise  # Re-raise other errors
        create_or_update_environment_variables("AZURE_IDENTITY_ROLES", f"{roles_to_assign}")

    except Exception as e:
        logging.error(f"Error assigning roles: {e}")

def create_or_update_environment_variables(key: str, value: str):

    # Load local settings
    file_path = "local.settings.json"

    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                settings = json.load(f)
            
            # Initialize "Values" if not present
            if "Values" not in settings:
                settings["Values"] = {}

            # Update values
            settings["Values"][key] = value

            # Save back
            with open(file_path, "w") as f:
                json.dump(settings, f, indent=4)

            logging.info(f"Updated local.settings.json with {key}={value}")

        except Exception as e:
            logging.error(f"Error updating local.settings.json: {e}")
    else:
        try:
            # Azure Function app environment variable update
            client = WebSiteManagementClient(DefaultAzureCredential(), os.environ["AZURE_SUBSCRIPTION_ID"])
            app_settings = {key: value}

            resource_group = os.environ["AZURE_FUNCTION_APP_RESOURCE_GROUP"]
            function_app_name = os.environ["AZURE_FUNCTION_APP_NAME"]

            # Update the application settings in Azure
            client.web_apps.update_application_settings(
                resource_group,
                function_app_name,
                {"properties": app_settings}
            )

            logging.info(f"Environment variables synced to Azure for {function_app_name}.")
        except Exception as e:
            logging.error(f"Error updating environment variables in Azure: {e}")

def create_or_update_environment_variables_remote(key: str, value: str):
    try:
        # Azure Function app environment variable update
        client = WebSiteManagementClient(DefaultAzureCredential(), os.environ["AZURE_SUBSCRIPTION_ID"])
        app_settings = {key: value}

        resource_group = os.environ["AZURE_FUNCTION_APP_RESOURCE_GROUP"]
        function_app_name = os.environ["AZURE_FUNCTION_APP_NAME"]

        # Update the application settings in Azure
        client.web_apps.update_application_settings(
            resource_group,
            function_app_name,
            {"properties": app_settings}
        )

        logging.info(f"Environment variables synced to Azure for {function_app_name}.")
    except Exception as e:
        logging.error(f"Error updating environment variables in Azure: {e}")
    
