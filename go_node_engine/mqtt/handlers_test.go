package mqtt

import (
	"encoding/json"
	"errors"
	"testing"
	"time"

	"go_node_engine/model"

	"gotest.tools/v3/assert"
)

func TestControlDeployContract_UnmarshalsIntoService(t *testing.T) {
	data := loadContract(t, "control_deploy.json")

	var service model.Service
	assert.NilError(t, json.Unmarshal(data, &service))

	assert.Equal(t, service.JobID, "65d200f3812caeb85e21ee19")
	assert.Equal(t, service.Sname, "app.ns.svc.inst")
	assert.Equal(t, service.Instance, 1)
	assert.Equal(t, service.Image, "docker.io/library/nginx:latest")
	assert.DeepEqual(t, service.Commands, []string{"nginx", "-g", "daemon off;"})
	assert.DeepEqual(t, service.Env, []string{"FOO=bar"})
	assert.Equal(t, service.Ports, "80:80")
	assert.Equal(t, service.Status, "NODE_SCHEDULED")
	assert.Equal(t, service.Runtime, "docker")
	assert.Equal(t, service.Platform, "linux")
	assert.Equal(t, service.Vcpus, 1)
	assert.Equal(t, service.Memory, 100)
	assert.DeepEqual(t, service.Architectures, []string{"amd64"})
	assert.Equal(t, service.OneShot, false)
	assert.Equal(t, service.Privileged, false)
	// microserviceID and instance_list are DB-only keys with no matching
	// json tag on model.Service, so Go silently drops them on the way in.
}

func TestDeployHandler_Success_ReportsInstantiationThenCreated(t *testing.T) {
	resetForTest(t)
	fc := installFakeClient(t)
	clientID = "n1"
	nodeIP = func() string { return "10.0.0.7" }

	proceed := make(chan struct{})
	rt := &fakeRuntime{
		deployFn: func(model.Service, func(model.Service)) error {
			<-proceed
			return nil
		},
	}
	provider := &fakeProvider{rt: rt}

	payload := []byte(`{"job_name":"app.ns.svc.inst","instance_number":1,"virtualization":"docker"}`)
	deployHandler(fc, &fakeMessage{payload: payload}, provider)

	calls := awaitPublish(t, fc, 1, 2*time.Second)
	var status ServiceStatus
	assert.NilError(t, json.Unmarshal([]byte(calls[0].payload), &status))
	assert.Equal(t, status.Status, model.SERVICE_INSTANTIATION)

	// While Deploy is still blocked, the service must show up in the
	// instantiating registry so the resource-monitoring loop keeps
	// reporting it and the cluster doesn't think the worker died.
	resources := model.InstantiatingResources(model.CONTAINER_RUNTIME)
	assert.Equal(t, len(resources), 1)
	assert.Equal(t, resources[0].Sname, "app.ns.svc.inst")

	close(proceed)

	calls = awaitPublish(t, fc, 2, 2*time.Second)
	assert.NilError(t, json.Unmarshal([]byte(calls[1].payload), &status))
	assert.Equal(t, status.Status, model.SERVICE_CREATED)

	deadline := time.Now().Add(time.Second)
	for len(model.InstantiatingResources(model.CONTAINER_RUNTIME)) != 0 && time.Now().Before(deadline) {
		time.Sleep(5 * time.Millisecond)
	}
	assert.Equal(t, len(model.InstantiatingResources(model.CONTAINER_RUNTIME)), 0)
}

func TestDeployHandler_ForwardsRuntimeTypeAndStatusCallback(t *testing.T) {
	resetForTest(t)
	fc := installFakeClient(t)
	clientID = "n1"
	nodeIP = func() string { return "10.0.0.7" }

	rt := &fakeRuntime{
		deployFn: func(service model.Service, notify func(model.Service)) error {
			service.Status = model.SERVICE_RUNNING
			notify(service)
			return nil
		},
	}
	provider := &fakeProvider{rt: rt}

	payload := []byte(`{"job_name":"app.ns.svc.inst","instance_number":1,"virtualization":"unikernel"}`)
	deployHandler(fc, &fakeMessage{payload: payload}, provider)

	calls := awaitPublish(t, fc, 3, 2*time.Second)
	var statuses []string
	for _, c := range calls[:3] {
		var s ServiceStatus
		assert.NilError(t, json.Unmarshal([]byte(c.payload), &s))
		statuses = append(statuses, s.Status)
	}
	assert.DeepEqual(t, statuses, []string{model.SERVICE_INSTANTIATION, model.SERVICE_RUNNING, model.SERVICE_CREATED})
	assert.DeepEqual(t, provider.requestedTypes(), []model.RuntimeType{model.UNIKERNEL_RUNTIME})
}

func TestDeployHandler_Error_ReportsFailedWithDetail(t *testing.T) {
	resetForTest(t)
	fc := installFakeClient(t)
	clientID = "n1"
	nodeIP = func() string { return "10.0.0.7" }

	deployErr := errors.New("image pull failed")
	rt := &fakeRuntime{deployFn: func(model.Service, func(model.Service)) error { return deployErr }}
	provider := &fakeProvider{rt: rt}

	payload := []byte(`{"job_name":"app.ns.svc.inst","instance_number":1,"virtualization":"docker"}`)
	deployHandler(fc, &fakeMessage{payload: payload}, provider)

	calls := awaitPublish(t, fc, 2, 2*time.Second)
	var status ServiceStatus
	assert.NilError(t, json.Unmarshal([]byte(calls[1].payload), &status))
	assert.Equal(t, status.Status, model.SERVICE_FAILED)
	assert.Equal(t, status.Detail, deployErr.Error())
}

func TestDeployHandler_MalformedJSON_NoPublishNoDeploy(t *testing.T) {
	resetForTest(t)
	fc := installFakeClient(t)

	deployed := false
	rt := &fakeRuntime{deployFn: func(model.Service, func(model.Service)) error { deployed = true; return nil }}
	provider := &fakeProvider{rt: rt}

	deployHandler(fc, &fakeMessage{payload: []byte("not-json")}, provider)

	time.Sleep(50 * time.Millisecond)
	assert.Equal(t, len(fc.publishCalls()), 0)
	assert.Assert(t, !deployed)
}

func TestDeployHandler_EmptyObject_StillDeploysZeroService(t *testing.T) {
	resetForTest(t)
	fc := installFakeClient(t)
	clientID = "n1"
	nodeIP = func() string { return "" }

	// json.Unmarshal of "{}" into model.Service succeeds with every field at its
	// zero value, so an empty control/deploy payload deploys a nameless,
	// zero-resource service instead of being rejected.
	deployed := false
	rt := &fakeRuntime{deployFn: func(model.Service, func(model.Service)) error { deployed = true; return nil }}
	provider := &fakeProvider{rt: rt}

	deployHandler(fc, &fakeMessage{payload: []byte("{}")}, provider)

	awaitPublish(t, fc, 2, 2*time.Second)
	assert.Assert(t, deployed)
	assert.DeepEqual(t, provider.requestedTypes(), []model.RuntimeType{""})
}

func TestDeleteHandler_Success_ReportsUndeployed(t *testing.T) {
	resetForTest(t)
	fc := installFakeClient(t)
	clientID = "n1"
	nodeIP = func() string { return "10.0.0.7" }

	rt := &fakeRuntime{undeployFn: func(string, int) error { return nil }}
	provider := &fakeProvider{rt: rt}

	payload := []byte(`{"job_name":"app.ns.svc.inst","instance_number":1,"virtualization":"docker"}`)
	deleteHandler(fc, &fakeMessage{payload: payload}, provider)

	calls := awaitPublish(t, fc, 1, 2*time.Second)
	var status ServiceStatus
	assert.NilError(t, json.Unmarshal([]byte(calls[0].payload), &status))
	assert.Equal(t, status.Status, model.SERVICE_UNDEPLOYED)
	assert.Equal(t, status.Sname, "app.ns.svc.inst")
}

func TestDeleteHandler_Error_PublishesNothing(t *testing.T) {
	resetForTest(t)
	fc := installFakeClient(t)

	rt := &fakeRuntime{undeployFn: func(string, int) error { return errors.New("not found") }}
	provider := &fakeProvider{rt: rt}

	payload := []byte(`{"job_name":"app.ns.svc.inst","instance_number":1,"virtualization":"docker"}`)
	deleteHandler(fc, &fakeMessage{payload: payload}, provider)

	time.Sleep(50 * time.Millisecond)
	assert.Equal(t, len(fc.publishCalls()), 0)
}

func TestDeleteHandler_MalformedJSON_NoUndeploy(t *testing.T) {
	resetForTest(t)
	fc := installFakeClient(t)

	undeployed := false
	rt := &fakeRuntime{undeployFn: func(string, int) error { undeployed = true; return nil }}
	provider := &fakeProvider{rt: rt}

	deleteHandler(fc, &fakeMessage{payload: []byte("not-json")}, provider)

	time.Sleep(50 * time.Millisecond)
	assert.Assert(t, !undeployed)
	assert.Equal(t, len(fc.publishCalls()), 0)
}
