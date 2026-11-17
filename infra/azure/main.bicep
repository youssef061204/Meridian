// MERIDIAN low-cost Azure footprint. Status: PREPARED, NOT COMPILED OR DEPLOYED from this repository
// (no Azure subscription, and the Bicep CLI was not run). Run `az bicep build -f infra/azure/main.bicep` first.
//
// Deploy into an empty resource group:
//   az deployment group create -g rg-meridian -f infra/azure/main.bicep \
//     -p sqlAdminPassword=<secret> budgetEmail=<you@example.com> clientIp=<your ip> analystObjectId=<your Entra object id>
//
// Cost shape (check the official pricing pages; prices change):
//   * Storage: ADLS Gen2, LRS, Hot. The Gold Parquet is ~25 MB, so storage is negligible.
//   * Synapse: NO dedicated SQL pool and NO Spark pool. Only the built-in serverless SQL pool,
//     billed per TB of data processed (with a small per-query minimum). Idle cost is zero.
//   * Event Hubs (Kafka endpoint): OFF by default. Standard tier has an hourly throughput-unit charge.
//   * Budget: email alerts at 50% and 90% actual, 100% forecast, of a monthly cap on this resource group.
targetScope = 'resourceGroup'

@description('Short prefix for resource names (lowercase letters and digits).')
@maxLength(10)
param prefix string = 'meridian'
param location string = resourceGroup().location
@description('Synapse SQL administrator login (SQL auth is for setup only; prefer Entra ID).')
param sqlAdminLogin string = 'meridianadmin'
@secure()
param sqlAdminPassword string
@description('Your public IP, allowed through the Synapse firewall. Leave empty to allow none.')
param clientIp string = ''
@description('Object ID of the Entra ID user or group that uploads Gold files (gets Storage Blob Data Contributor).')
param analystObjectId string = ''
@description('Monthly budget for this resource group, in the billing currency.')
param monthlyBudget int = 10
param budgetEmail string
@description('Budget start: first day of the current month, yyyy-MM-01.')
param budgetStartDate string = '${utcNow('yyyy-MM')}-01'
@description('Provision an Event Hubs namespace with the Kafka endpoint for the replay path. Adds a fixed hourly cost.')
param enableEventHubs bool = false

var suffix = uniqueString(resourceGroup().id)
var storageName = toLower(take('${prefix}${suffix}', 24))
var blobDataReader = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
var blobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    isHnsEnabled: true              // ADLS Gen2
    accessTier: 'Hot'
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false     // Entra ID only; no account keys in scripts
    supportsHttpsTrafficOnly: true
  }
}

resource blob 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: { deleteRetentionPolicy: { enabled: true, days: 7 } }
}

resource gold 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blob
  name: 'gold'
}

resource synapseFs 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blob
  name: 'synapse'
}

resource synapse 'Microsoft.Synapse/workspaces@2021-06-01' = {
  name: '${prefix}-syn-${suffix}'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    defaultDataLakeStorage: {
      accountUrl: storage.properties.primaryEndpoints.dfs
      filesystem: synapseFs.name
      resourceId: storage.id
    }
    sqlAdministratorLogin: sqlAdminLogin
    sqlAdministratorLoginPassword: sqlAdminPassword
    publicNetworkAccess: 'Enabled'
  }
}

resource firewallClient 'Microsoft.Synapse/workspaces/firewallRules@2021-06-01' = if (!empty(clientIp)) {
  parent: synapse
  name: 'client'
  properties: { startIpAddress: clientIp, endIpAddress: clientIp }
}

// Serverless SQL reads Gold Parquet with the workspace identity: read-only on the storage account.
resource synapseReadsGold 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, synapse.id, blobDataReader)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobDataReader)
    principalId: synapse.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource analystWritesGold 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(analystObjectId)) {
  scope: storage
  name: guid(storage.id, analystObjectId, blobDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobDataContributor)
    principalId: analystObjectId
  }
}

resource eventHubs 'Microsoft.EventHub/namespaces@2024-01-01' = if (enableEventHubs) {
  name: '${prefix}-eh-${suffix}'
  location: location
  sku: { name: 'Standard', tier: 'Standard', capacity: 1 }   // the Kafka endpoint needs Standard or higher
  properties: { minimumTlsVersion: '1.2', disableLocalAuth: true }
}

resource replayTopic 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = if (enableEventHubs) {
  parent: eventHubs
  name: 'meridian.replay'
  properties: { partitionCount: 1, messageRetentionInDays: 1 }   // 1 partition: total order, as locally
}

resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: '${prefix}-monthly'
  properties: {
    category: 'Cost'
    amount: monthlyBudget
    timeGrain: 'Monthly'
    timePeriod: { startDate: budgetStartDate }
    notifications: {
      actual50: { enabled: true, operator: 'GreaterThan', threshold: 50, contactEmails: [budgetEmail], thresholdType: 'Actual' }
      actual90: { enabled: true, operator: 'GreaterThan', threshold: 90, contactEmails: [budgetEmail], thresholdType: 'Actual' }
      forecast100: { enabled: true, operator: 'GreaterThan', threshold: 100, contactEmails: [budgetEmail], thresholdType: 'Forecasted' }
    }
  }
}

output storageAccount string = storage.name
output goldUrl string = '${storage.properties.primaryEndpoints.dfs}${gold.name}'
output serverlessSqlEndpoint string = synapse.properties.connectivityEndpoints.sqlOnDemand
output eventHubsKafkaBootstrap string = enableEventHubs ? '${eventHubs.name}.servicebus.windows.net:9093' : ''
