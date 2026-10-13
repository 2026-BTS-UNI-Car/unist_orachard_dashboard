#ifndef UBTS_PRIVATE_DERIVED_RUNTIME_HPP
#define UBTS_PRIVATE_DERIVED_RUNTIME_HPP
#include "derived_catalog.hpp"

namespace ubts { namespace research { namespace derived_catalog {
// SDK-free owner. It holds ONLY copied native bytes/metadata, never a Grid,
// bridge or Node pointer. DI providers are supplied by the caller, not certified.
struct Bootstrap {
  bool enabled=false;Context context;Digest digest;ReceiptVerifier verifier;
};
struct RuntimeStatus {
  std::string scope="UNQUALIFIED_AD8_SDK_PREVIEW",last_result;
  CoordinatorView coordinator;
  std::uint64_t last_capture_sequence=0,last_batch_id=0,pending_sequence=0,pending_batch_id=0;
  std::size_t active_captures=0,active_provider_operations=0;
  bool closed=false;
};
class Runtime {
 private:
  struct Batch {Frame frame;std::vector<Request> requests;bool drafted=false;};
  struct Shared {
    explicit Shared(Bootstrap b):context(std::move(b.context)),coordinator(b.enabled,std::move(b.digest),std::move(b.verifier)) {
      if(b.enabled){Require(context.source=="UNQUALIFIED_AD8_SDK_PREVIEW","research_DI_source_label");
        Require(coordinator.start(context)==State::ACCEPTED,"runtime_context_start");}
    }
    const Context context;Coordinator coordinator;mutable std::mutex gate;
    std::mutex pump_mutex;std::unique_ptr<Batch> pending;
    bool stopped=false,closed=false;std::uint64_t sequence=0,last_batch_id=0;
    std::size_t captures=0,providers=0;std::string result="idle";
  };
 public:
  class CaptureGuard {
   public:
    CaptureGuard()=default;CaptureGuard(const CaptureGuard&)=delete;CaptureGuard& operator=(const CaptureGuard&)=delete;
    CaptureGuard(CaptureGuard&& b) noexcept:state_(std::move(b.state_)),sequence_(b.sequence_),submitted_(b.submitted_.load()){b.sequence_=0;}
    CaptureGuard& operator=(CaptureGuard&&)=delete;
    ~CaptureGuard(){if(state_){std::lock_guard<std::mutex> l(state_->gate);--state_->captures;}}
    explicit operator bool()const{return bool(state_);}
    std::uint64_t sequence()const{return sequence_;}
   private:
    friend class Runtime;CaptureGuard(std::shared_ptr<Shared> s,std::uint64_t seq):state_(std::move(s)),sequence_(seq){}
    std::shared_ptr<Shared> state_;std::uint64_t sequence_=0;mutable std::atomic<bool> submitted_{false};
  };
  class RuntimeWireLease {
   public:
    RuntimeWireLease()=default;RuntimeWireLease(const RuntimeWireLease&)=delete;RuntimeWireLease& operator=(const RuntimeWireLease&)=delete;
    RuntimeWireLease(RuntimeWireLease&&)=default;
    RuntimeWireLease& operator=(RuntimeWireLease&& b) noexcept {
      if(this!=&b){lease_=WireLease{};state_.reset();state_=std::move(b.state_);lease_=std::move(b.lease_);}return *this;
    }
    std::string_view bytes()const{return lease_.bytes();} // borrowed for this lease only
   private:
    friend class Runtime;
    RuntimeWireLease(std::shared_ptr<Shared> s,WireLease lease):state_(std::move(s)),lease_(std::move(lease)){}
    // Reverse destruction order frees charged wire before the last owner pin.
    std::shared_ptr<Shared> state_;WireLease lease_;
  };
  explicit Runtime(Bootstrap b={}):state_(std::make_shared<Shared>(std::move(b))){}
  RuntimeStatus status()const {
    const auto s=state_;std::lock_guard<std::mutex> l(s->gate);RuntimeStatus result;
    result.coordinator=s->coordinator.view();result.last_result=s->result;
    result.last_capture_sequence=s->sequence;result.last_batch_id=s->last_batch_id;
    if(s->pending){result.pending_sequence=s->pending->frame.sequence;result.pending_batch_id=s->pending->frame.batch_id;}
    result.active_captures=s->captures;result.active_provider_operations=s->providers;result.closed=s->closed;return result;
  }
  CaptureGuard begin_capture() {
    const auto s=state_;std::lock_guard<std::mutex> l(s->gate);const auto view=s->coordinator.view();
    if(!view.enabled)return {};
    if(s->stopped||s->closed||view.stopped)return {};
    // ONE admitted pending batch cannot be superseded by an ever-newer body.
    // Existing published catalog stays readable through hash/archive backpressure.
    if(s->pending||s->captures){s->result="DEFER_existing_owned_batch";return {};}
    Require(s->sequence!=UINT64_MAX,"capture_sequence_overflow");
    // A result-string allocation must precede guard/accounting admission.
    s->result="capturing_one_SDK_metadata_batch";++s->sequence;++s->captures;return CaptureGuard(s,s->sequence);
  }
  State capture_raw(const CaptureGuard& guard,BodyRef body,const std::function<void(Writer&)>& fill,Request* result) {
    Require(guard.state_==state_&&bool(guard)&&!guard.submitted_.load(),"unsealed_owned_capture_guard_required");
    {std::lock_guard<std::mutex> l(state_->gate);state_->result="copying_owned_native_raw";}
    return state_->coordinator.capture_raw(std::move(body),fill,result);
  }
  State submit(const CaptureGuard& guard,Frame frame,std::vector<Request> requests) {
    const auto s=state_;Require(guard.state_==s&&bool(guard),"owned_capture_guard_required");
    std::lock_guard<std::mutex> l(s->gate);
    if(s->stopped||s->closed||s->coordinator.stopped())return State::STOPPED;
    if(guard.submitted_.load())return State::REJECTED;
    if(s->pending){s->result="DEFER_existing_owned_batch";return State::DEFERRED;}
    Require(frame.context==s->context&&frame.sequence==guard.sequence_&&frame.batch_id==guard.sequence_&&
      frame.entries.size()<=4096&&requests.size()==frame.entries.size()&&frame.observation.latest_local_slam_ticks.size()<=4096,
      "bounded_exact_owned_metadata_batch");
    for(std::size_t i=0;i<requests.size();++i) {
      if(!(frame.entries[i].body==requests[i].body)||!s->coordinator.owns(requests[i])) {
        s->coordinator.fatal_source_fault();s->stopped=true;s->result="fatal_owned_request_binding_mismatch";return State::REJECTED;
      }
    }
    auto admitted=std::make_unique<Batch>(Batch{std::move(frame),std::move(requests),false});
    guard.submitted_.store(true);
    s->last_batch_id=admitted->frame.batch_id;s->pending=std::move(admitted);
    s->result="pending_hash_or_archive_ACK";return State::ACCEPTED;
  }
  bool stopped()const{return state_->coordinator.stopped();} // allocation-free Stop fence
  State pump() {
    const auto s=state_;
    try {
    if(!s->coordinator.view().enabled)return State::OFF;
    std::unique_lock<std::mutex> pump(s->pump_mutex,std::try_to_lock);
    if(!pump.owns_lock())return State::DEFERRED;
    ProviderGuard provider(s);
    if(!provider)return State::STOPPED;
    auto raw=s->coordinator.raw_requests();
    try {if(!raw.empty())s->coordinator.finalize(&raw.front());}
    catch(const std::bad_alloc&){throw;} // handled by the outer pressure boundary
    catch(const std::exception&){std::lock_guard<std::mutex> l(s->gate);s->result="fatal_finalization_retained_raw";s->pending.reset();return State::REJECTED;}
    std::lock_guard<std::mutex> l(s->gate);
    if(s->stopped||s->closed||s->coordinator.stopped()){s->pending.reset();s->result="stopped_archive_only";return State::STOPPED;}
    if(!s->pending){s->result="idle_or_partial_batch_archive";return State::DEFERRED;}
    if(!s->pending->drafted) {
      for(std::size_t i=0;i<s->pending->requests.size();++i) {
        auto& request=s->pending->requests[i];
        if(!request.nonce.empty()&&!s->coordinator.resolve(&request)){s->result="waiting_owned_body_hash";return State::DEFERRED;}
        if(!Hex64(request.body.sha256)){s->result="waiting_owned_body_hash";return State::DEFERRED;}
        s->pending->frame.entries[i].body=request.body;
      }
      const auto drafted=s->coordinator.draft(s->pending->frame);
      if(drafted!=State::ACCEPTED){s->result="draft_rejected_or_capacity_DEFER";return drafted;}
      s->pending->drafted=true;
    }
    const auto published=s->coordinator.publish();
    if(published==State::ACCEPTED){s->pending.reset();s->result="published_complete_copied_batch";}
    else s->result="waiting_exact_archive_ACK";
    return published;
    }catch(const std::bad_alloc&){
      // Covers copied status/context, raw-request enumeration and draft copies.
      // No new allocation, integrity forgiveness or pending-batch drop here.
      return s->coordinator.stopped()?State::STOPPED:State::DEFERRED;
    }
  }
  std::vector<Request> archive_requests()const{return state_->coordinator.archive_requests();}
  RuntimeWireLease chunk(const Request& request,std::size_t offset,std::size_t cells) {
    const auto s=state_;return RuntimeWireLease(s,s->coordinator.chunk(request,offset,cells));
  }
  bool ack(const Ack& ack) {const auto s=state_;ProviderGuard provider(s);return provider&&s->coordinator.ack(ack);}
  Page page(const CatalogIdentity& identity,std::size_t ordinal)const {
    return state_->coordinator.page(identity.context,identity.sequence,identity.batch_id,ordinal);
  }
  void stop() {
    const auto s=state_;s->coordinator.stop(); // no digest/verifier/pump wait
    std::lock_guard<std::mutex> l(s->gate);s->stopped=true;s->result="stopped_archive_only";
    // SDK-free metadata release only. Active raw copies and leases stay owned.
    s->pending.reset();
  }
  void fatal_source_fault() {const auto s=state_;s->coordinator.fatal_source_fault();std::lock_guard<std::mutex> l(s->gate);
    s->stopped=true;s->pending.reset();s->result="fatal_source_integrity_retained_raw";}
  State close() {
    stop();const auto s=state_;std::lock_guard<std::mutex> l(s->gate);const auto usage=s->coordinator.view();
    if(usage.resident_bytes||usage.wire_bytes||usage.pending_requests||s->captures||s->providers){s->result="close_DEFER_owned_raw_wire_or_active_provider";return State::DEFERRED;}
    s->closed=true;s->result="gracefully_closed_retained_catalog";return State::ACCEPTED;
  }
 private:
  class ProviderGuard {
   public:
    explicit ProviderGuard(std::shared_ptr<Shared> s):state_(std::move(s)){
      std::lock_guard<std::mutex> l(state_->gate);if(!state_->closed){++state_->providers;active_=true;}}
    ~ProviderGuard(){if(active_){std::lock_guard<std::mutex> l(state_->gate);--state_->providers;}}
    explicit operator bool()const{return active_;}
   private:std::shared_ptr<Shared> state_;bool active_=false;
  };
  std::shared_ptr<Shared> state_;
};
}}}
#endif
