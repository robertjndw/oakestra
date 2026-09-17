package clusterlink

import (
	"sync"

	"go_node_engine/model"
)

// fakeRuntime is a ServiceRuntime double whose Deploy/Undeploy behavior the
// test controls per case.
type fakeRuntime struct {
	deployFn   func(service model.Service, statusChangeNotificationHandler func(service model.Service)) error
	undeployFn func(sname string, instance int) error
}

func (r *fakeRuntime) Deploy(service model.Service, statusChangeNotificationHandler func(service model.Service)) error {
	if r.deployFn == nil {
		return nil
	}
	return r.deployFn(service, statusChangeNotificationHandler)
}

func (r *fakeRuntime) Undeploy(sname string, instance int) error {
	if r.undeployFn == nil {
		return nil
	}
	return r.undeployFn(sname, instance)
}

// fakeProvider is a RuntimeProvider double that always returns rt and
// records which runtime types were requested, so tests can assert
// deployHandler/deleteHandler forward service.Runtime correctly.
type fakeProvider struct {
	rt ServiceRuntime

	mu        sync.Mutex
	requested []model.RuntimeType
}

func (p *fakeProvider) GetRuntime(rt model.RuntimeType) ServiceRuntime {
	p.mu.Lock()
	p.requested = append(p.requested, rt)
	p.mu.Unlock()
	return p.rt
}

func (p *fakeProvider) requestedTypes() []model.RuntimeType {
	p.mu.Lock()
	defer p.mu.Unlock()
	return append([]model.RuntimeType(nil), p.requested...)
}
