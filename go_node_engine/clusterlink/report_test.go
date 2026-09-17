package clusterlink

import (
	"encoding/json"
	"testing"
	"time"

	"go_node_engine/config"
	"go_node_engine/model"

	"gotest.tools/v3/assert"
)

func TestReportServiceStatus_MatchesContract(t *testing.T) {
	mb := installBus(t)
	nodeIP = func() string { return "10.0.0.7" }

	ReportServiceStatus(model.Service{
		Sname:        "app.ns.svc.inst",
		Status:       model.SERVICE_CREATED,
		StatusDetail: "",
		Instance:     1,
	})

	calls := awaitPublish(t, mb, 1, time.Second)
	assert.Equal(t, calls[0].Topic, "nodes/n1/job")
	// Compare the whole object, not a few fields, so an accidental extra key
	// fails too.
	assertJSONEqual(t, calls[0].Payload, loadContract(t, "job_status.json"))
}

func TestReportServiceStatus_EmptyNodeIP_PublishesEmptyString(t *testing.T) {
	mb := installBus(t)
	nodeIP = func() string { return "" }

	ReportServiceStatus(model.Service{Sname: "app.ns.svc.inst", Status: model.SERVICE_CREATED, Instance: 1})

	calls := awaitPublish(t, mb, 1, time.Second)
	var status ServiceStatus
	assert.NilError(t, json.Unmarshal(calls[0].Payload, &status))
	assert.Equal(t, status.Publicip, "")
}

func TestReportServiceResources_WrapsInServicesArray(t *testing.T) {
	mb := installBus(t)

	ReportServiceResources([]model.Resources{{
		Cpu: "1.50", Memory: "2.00", Disk: "0", Logs: "hello\n",
		Sname: "app.ns.svc.inst", Runtime: "docker", Instance: 1, Status: "RUNNING",
	}})

	calls := awaitPublish(t, mb, 1, time.Second)
	assert.Equal(t, calls[0].Topic, "nodes/n1/jobs/resources")
	assertJSONEqual(t, calls[0].Payload, loadContract(t, "jobs_resources.json"))
}

func TestReportServiceResources_NilVsEmptySliceMarshalDiffers(t *testing.T) {
	mb := installBus(t)

	// encoding/json marshals a nil slice as `null` but an empty, non-nil slice
	// as `[]`. ReportServiceResources never normalizes this, so a nil resources
	// argument and a monitoring tick with zero services look different on the
	// wire.
	ReportServiceResources(nil)
	nilCalls := awaitPublish(t, mb, 1, time.Second)
	assert.Equal(t, string(nilCalls[0].Payload), `{"services":null}`)

	ReportServiceResources([]model.Resources{})
	emptyCalls := awaitPublish(t, mb, 2, time.Second)
	assert.Equal(t, string(emptyCalls[1].Payload), `{"services":[]}`)
}

func TestReportNodeInformation_KeySetIncludesUntaggedFields(t *testing.T) {
	mb := installBus(t)

	node := model.Node{
		Id:              "node1",
		Host:            "worker-1",
		Ip:              "10.0.0.7",
		Port:            "",
		SystemInfo:      map[string]string{"kernel_version": "6.1.0", "os": "linux"},
		CpuUsage:        12.5,
		CpuCores:        4,
		CpuArch:         "amd64",
		MemoryUsed:      40.25,
		MemoryMB:        8192,
		DiskInfo:        map[string]string{"/": "50"},
		NetworkInfo:     map[string]string{"eth0": "10.0.0.7"},
		GpuDriver:       "",
		GpuUsage:        0,
		GpuCores:        0,
		GpuTemp:         0,
		GpuMemUsage:     0,
		GpuTotMem:       0,
		Technology:      []model.RuntimeType{model.CONTAINER_RUNTIME},
		SupportedAddons: []model.AddonType{},
		CSIDrivers:      []config.CSIDriverType{},
		Overlay:         false,
		OverlaySocket:   "/etc/netmanager/netmanager.sock",
		LogDirectory:    "/tmp",
		NetManagerPort:  0,
		ClusterAddress:  "0.0.0.0",
	}

	ReportNodeInformation(node)

	calls := awaitPublish(t, mb, 1, time.Second)
	assert.Equal(t, calls[0].Topic, "nodes/n1/information")
	// The contract fixture carries five untagged Go fields (Overlay,
	// OverlaySocket, LogDirectory, NetManagerPort, ClusterAddress) that leak
	// onto the wire under their exported Go names because model.Node has no
	// json tag for them.
	assertJSONEqual(t, calls[0].Payload, loadContract(t, "node_information.json"))

	var asMap map[string]any
	assert.NilError(t, json.Unmarshal(calls[0].Payload, &asMap))
	assert.Equal(t, len(asMap), 26)
}

func TestStatusConstants_MatchContract(t *testing.T) {
	data := loadContract(t, "statuses.json")
	var statuses []string
	assert.NilError(t, json.Unmarshal(data, &statuses))

	assert.DeepEqual(t, statuses, []string{
		model.SERVICE_CREATING,
		model.SERVICE_CREATED,
		model.SERVICE_FAILED,
		model.SERVICE_DEAD,
		model.SERVICE_COMPLETED,
		model.SERVICE_UNDEPLOYED,
		model.SERVICE_RUNNING,
		model.SERVICE_INSTANTIATION,
		model.SERVICE_UNKNOWN,
	})
}
