// Package clusterlink is NodeEngine's side of the cluster link. It deploys
// and deletes services on command from the cluster orchestrator and reports
// status, resources, and node info back over a messaging.Bus.
package clusterlink

import (
	"encoding/json"
	"fmt"
	"go_node_engine/logger"
	"go_node_engine/model"

	messaging "github.com/oakestra/oakestra/libraries/oakestra_messaging_go"
)

var bus messaging.Bus
var nodeID = ""

// ServiceRuntime is the subset of a virtualization runtime the control handlers use.
type ServiceRuntime interface {
	Deploy(service model.Service, statusChangeNotificationHandler func(service model.Service)) error
	Undeploy(sname string, instance int) error
}

// RuntimeProvider resolves the runtime for a runtime type.
type RuntimeProvider interface {
	GetRuntime(runtime model.RuntimeType) ServiceRuntime
}

type RuntimeProviderFunc func(model.RuntimeType) ServiceRuntime

func (f RuntimeProviderFunc) GetRuntime(rt model.RuntimeType) ServiceRuntime { return f(rt) }

// AdaptRuntimeProvider wraps a getter such as (*virtualization.RuntimeManager).GetRuntime, whose
// return type lives in an internal package this package cannot import.
func AdaptRuntimeProvider[R ServiceRuntime](get func(model.RuntimeType) R) RuntimeProvider {
	return RuntimeProviderFunc(func(rt model.RuntimeType) ServiceRuntime { return get(rt) })
}

// Indirection so tests can inject a fake node IP and avoid model.GetNodeInfo(), which reads
// /etc/oakestra and exits the process when that fails.
var nodeIP = func() string { return model.GetNodeInfo().Ip }

// ClientID returns the bus client ID this node connects with, so callers can
// build the transport before calling Init.
func ClientID(id string) string {
	return id + "-ne"
}

// Init wires the given bus to this node's control topics. It does not
// connect the bus; the caller connects once every domain package has
// finished subscribing.
func Init(b messaging.Bus, id string, runtimeManager RuntimeProvider) {
	if nodeID != "" {
		logger.InfoLogger().Printf("clusterlink already initialized no need for any further initialization")
		return
	}

	bus = b
	nodeID = id

	if err := bus.Subscribe(fmt.Sprintf("nodes/%s/control/deploy", nodeID), func(msg messaging.Message) {
		deployHandler(msg, runtimeManager)
	}); err != nil {
		logger.ErrorLogger().Printf("ERROR: unable to subscribe to control/deploy: %v", err)
	}
	if err := bus.Subscribe(fmt.Sprintf("nodes/%s/control/delete", nodeID), func(msg messaging.Message) {
		deleteHandler(msg, runtimeManager)
	}); err != nil {
		logger.ErrorLogger().Printf("ERROR: unable to subscribe to control/delete: %v", err)
	}
}

func publish(suffix string, payload string) {
	if bus == nil {
		logger.ErrorLogger().Printf("ERROR: clusterlink publish called before Init: %s", suffix)
		return
	}
	topic := fmt.Sprintf("nodes/%s/%s", nodeID, suffix)
	logger.InfoLogger().Printf("MQTT - publish to - %s - the payload - %s", topic, payload)
	if err := bus.Publish(topic, []byte(payload)); err != nil {
		logger.ErrorLogger().Printf("ERROR: MQTT PUBLISH: %s", err)
	}
}

func deployHandler(msg messaging.Message, runtimeManager RuntimeProvider) {
	logger.InfoLogger().Printf("Received deployment request with payload: %s", string(msg.Payload))
	service := model.Service{}
	err := json.Unmarshal(msg.Payload, &service)
	logger.InfoLogger().Printf("%+v", service)
	if err != nil {
		logger.ErrorLogger().Printf("ERROR: unable to unmarshal cluster orch request: %v", err)
		return
	}
	// Without this, a long image pull looks like a dead worker to the cluster.
	service.Status = model.SERVICE_INSTANTIATION
	ReportServiceStatus(service)
	model.TrackInstantiating(service)

	//handle deployment in background
	go func() {
		defer model.UntrackInstantiating(service.Sname, service.Instance)
		runtime := runtimeManager.GetRuntime(model.RuntimeType(service.Runtime))
		err = runtime.Deploy(service, ReportServiceStatus)
		service.Status = model.SERVICE_CREATED
		if err != nil {
			logger.ErrorLogger().Printf("ERROR during app deployment: %v", err)
			service.StatusDetail = err.Error()
			service.Status = model.SERVICE_FAILED
		}
		ReportServiceStatus(service)
	}()
}

func deleteHandler(msg messaging.Message, runtimeManager RuntimeProvider) {
	logger.InfoLogger().Printf("Received undeployment request with payload: %s", string(msg.Payload))
	service := model.Service{}
	err := json.Unmarshal(msg.Payload, &service)
	if err != nil {
		logger.ErrorLogger().Printf("ERROR: unable to unmarshal cluster orch request: %v", err)
		return
	}
	go func() {
		runtime := runtimeManager.GetRuntime(model.RuntimeType(service.Runtime))
		err = runtime.Undeploy(service.Sname, service.Instance)
		if err != nil {
			logger.ErrorLogger().Printf("Unable to undeploy application: %s", err.Error())
			return
		}
		service.Status = model.SERVICE_UNDEPLOYED
		ReportServiceStatus(service)
	}()
}

// ServiceStatus is the wire format published on nodes/<id>/job.
type ServiceStatus struct {
	Sname    string `json:"sname"`
	Status   string `json:"status"`
	Detail   string `json:"status_detail"`
	Instance int    `json:"instance"`
	Publicip string `json:"publicip"`
}

// ServiceResources is the wire format published on nodes/<id>/jobs/resources.
type ServiceResources struct {
	Services []model.Resources `json:"services"`
}

// ReportServiceStatus reports the status of the services
func ReportServiceStatus(service model.Service) {
	reportStatusStruct := ServiceStatus{
		Sname:    service.Sname,
		Status:   service.Status,
		Detail:   service.StatusDetail,
		Instance: service.Instance,
		Publicip: nodeIP(),
	}
	jsonmsg, err := json.Marshal(reportStatusStruct)
	if err != nil {
		logger.ErrorLogger().Printf("ERROR: unable to report service status: %v", err)
	}
	publish("job", string(jsonmsg))
}

// ReportServiceResources reports the resources of the services
func ReportServiceResources(services []model.Resources) {
	jsonmsg, err := json.Marshal(ServiceResources{Services: services})
	if err != nil {
		logger.ErrorLogger().Printf("ERROR: unable to report services resources: %v", err)
	}
	publish("jobs/resources", string(jsonmsg))
}

// ReportNodeInformation reports the information of the node in the broker
func ReportNodeInformation(node model.Node) {
	data, err := json.Marshal(node)
	if err != nil {
		logger.ErrorLogger().Printf("ERROR: error gathering node info")
	}
	publish("information", string(data))
}
