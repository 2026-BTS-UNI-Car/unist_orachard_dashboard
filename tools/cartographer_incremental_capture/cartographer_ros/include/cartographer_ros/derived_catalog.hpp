#ifndef UBTS_PRIVATE_DERIVED_CATALOG_HPP
#define UBTS_PRIVATE_DERIVED_CATALOG_HPP

// DEFAULT-OFF standalone research mechanism. No ROS/SDK/physical admission.
// Digest and receipt verification are explicit trusted dependencies. Their real
// IPC/crypto/storage/source-ownership implementations are not supplied here.
#include <array>
#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <functional>
#include <limits>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <tuple>
#include <vector>

namespace ubts { namespace research { namespace derived_catalog {
struct Context {
  std::string epoch, source, frame;
  std::uint64_t generation=0;
  bool operator==(const Context& b) const {return std::tie(epoch,source,frame,generation)==std::tie(b.epoch,b.source,b.frame,b.generation);}
};
struct Geometry {
  std::uint32_t i0=0,i1=0,n0=0,n1=0;
  std::array<double,3> limits{{0.,0.,0.}}; // resolution,native maxX,maxY
  std::array<double,7> local_pose{{0.,0.,0.,0.,0.,0.,1.}};
  bool operator==(const Geometry& b) const {return i0==b.i0&&i1==b.i1&&n0==b.n0&&n1==b.n1&&
      std::memcmp(limits.data(),b.limits.data(),sizeof(limits))==0&&
      std::memcmp(local_pose.data(),b.local_pose.data(),sizeof(local_pose))==0;}
  std::size_t cells() const {return std::size_t(n0)*std::size_t(n1);}
};
using Key=std::pair<std::int32_t,std::int32_t>;
struct BodyRef {
  Context context; Key key{0,0}; std::uint64_t version=0,body_id=0;
  bool finished=false; Geometry geometry; std::string sha256;
  bool operator==(const BodyRef& b) const {return context==b.context&&key==b.key&&version==b.version&&
      body_id==b.body_id&&finished==b.finished&&geometry==b.geometry&&sha256==b.sha256;}
};
struct Request {
  std::string nonce; BodyRef body;
  bool operator==(const Request& b) const {return nonce==b.nonce&&body==b.body;}
};
struct Ack {Request request;std::string receipt;};
struct Entry {BodyRef body;std::array<double,7> global_pose{{0.,0.,0.,0.,0.,0.,1.}};};
struct Observation {
  std::int64_t captured_RAW_ns=0,captured_ROS_ns=0;
  std::map<std::int32_t,std::int64_t> latest_local_slam_ticks;
};
struct Frame {
  // sequence is the capture/catalog identity; batch_id binds one immutable
  // metadata response. It is NOT a native pose graph revision or certificate.
  Context context;std::uint64_t sequence=0,batch_id=0;Observation observation;
  std::vector<Entry> entries;
};
struct Page {Context context;std::uint64_t sequence=0,batch_id=0;std::size_t total=0,ordinal=0;
  std::vector<Entry> entries;};
struct CatalogIdentity {Context context;std::uint64_t sequence=0,batch_id=0;};
struct CoordinatorView {
  bool enabled=false,stopped=true,fatal=false;Context context;
  std::optional<CatalogIdentity> published;
  std::size_t resident_bytes=0,wire_bytes=0,pending_requests=0,raw_requests=0,finalizing_requests=0,archive_requests=0;
};
struct Limits {
  std::size_t body_cells=4194304,resident_bytes=20971520,pending_bodies=8;
  std::size_t wire_bytes=81920,chunk_cells=16384,catalog_entries=4096,page_entries=64,stored_refs=8192,trajectory_watermarks=4096;
};
enum class State {OFF,ACCEPTED,UNCHANGED,DEFERRED,REJECTED,STOPPED};
using Digest=std::function<std::string(std::string_view)>;
using ReceiptVerifier=std::function<bool(const Ack&)>;

inline bool Hex64(const std::string& s) {
  if(s.size()!=64)return false;
  for(char c:s)if(!((c>='0'&&c<='9')||(c>='a'&&c<='f')))return false;
  return true;
}
inline void Require(bool ok,const char* why){if(!ok)throw std::invalid_argument(why);}
inline void ValidateContext(const Context& c) {
  Require(!c.epoch.empty()&&c.epoch.size()<=64&&!c.source.empty()&&c.source.size()<=128&&
      !c.frame.empty()&&c.frame.size()<=128&&c.generation>0,"bounded_unqualified_context");
}
inline void ValidateGeometry(const Geometry& g,std::size_t cap) {
  Require(g.n0>0&&g.n1>0&&std::uint64_t(g.n0)*g.n1<=cap,"native_body_cell_cap");
  Require(std::uint64_t(g.i0)+g.n0<=UINT32_MAX&&std::uint64_t(g.i1)+g.n1<=UINT32_MAX,"crop_integer_overflow");
  for(double v:g.limits)Require(std::isfinite(v),"nonfinite_limits");
  Require(g.limits[0]>0.,"positive_resolution");
  for(double v:g.local_pose)Require(std::isfinite(v),"nonfinite_local_pose");
}
inline std::string Binding(const BodyRef& b) {
  // Exact private metadata identity, not a source certificate. Binary doubles
  // preserve signed-zero and bit identity; no pose normalization or math.
  std::ostringstream out;
  for(const auto* s:{&b.context.epoch,&b.context.source,&b.context.frame,&b.sha256})out<<s->size()<<':'<<*s<<';';
  out<<b.context.generation<<':'<<b.key.first<<':'<<b.key.second<<':'<<b.version<<':'<<b.body_id<<':'<<b.finished<<':';
  out<<b.geometry.i0<<':'<<b.geometry.i1<<':'<<b.geometry.n0<<':'<<b.geometry.n1<<':';
  out.write(reinterpret_cast<const char*>(b.geometry.limits.data()),sizeof(b.geometry.limits));
  out.write(reinterpret_cast<const char*>(b.geometry.local_pose.data()),sizeof(b.geometry.local_pose));
  return out.str();
}
struct Accounting {std::mutex mutex;std::size_t resident=0,wire=0,slots=0;};
class WireLease {
 public:
  WireLease()=default;WireLease(const WireLease&)=delete;WireLease& operator=(const WireLease&)=delete;
  WireLease(WireLease&& b) noexcept : budget_(std::move(b.budget_)),bytes_(std::move(b.bytes_)),charged_(b.charged_) {b.charged_=0;}
  WireLease& operator=(WireLease&& b) noexcept {if(this!=&b){Release();budget_=std::move(b.budget_);bytes_=std::move(b.bytes_);charged_=b.charged_;b.charged_=0;}return *this;}
  ~WireLease(){Release();}
  std::string_view bytes()const{return {bytes_.get(),charged_};} // borrowed only for this lease
 private:
  friend class Coordinator;
  WireLease(std::shared_ptr<Accounting> a,std::unique_ptr<char[]> b,std::size_t n):budget_(std::move(a)),bytes_(std::move(b)),charged_(n){}
  void Release(){if(charged_){bytes_.reset();std::lock_guard<std::mutex> l(budget_->mutex);budget_->wire-=charged_;charged_=0;}}
  std::shared_ptr<Accounting> budget_;std::unique_ptr<char[]> bytes_;std::size_t charged_=0;
};
class Writer {
 public:
  // Synchronous borrowed fill capability only: do not retain its address or
  // references after fill returns. Copy/move cannot extend this lifetime.
  Writer(const Writer&)=delete;Writer& operator=(const Writer&)=delete;
  Writer(Writer&&)=delete;Writer& operator=(Writer&&)=delete;
  void cell(float probability,bool known) {
    Require(!stopped_->load(),"capture_cancelled_by_Stop");
    Require(next_<cells_&&std::isfinite(probability)&&probability>=0.f&&probability<=1.f&&
        (known||probability==0.f),"invalid_native_float_mask");
    static_assert(sizeof(float)==4&&std::numeric_limits<float>::is_iec559,"IEEE754 float32 required");
    std::uint32_t bits;std::memcpy(&bits,&probability,4);
    // Unknown is the exact +0.f placeholder in legacy/native Python/JS; known
    // -0.f remains valid and its original float32 sign bit stays unchanged.
    Require(known||bits==0,"unknown_native_bits_must_be_positive_zero");
    for(int byte=0;byte<4;++byte)raw_[4*next_+byte]=char((bits>>(8*byte))&255);
    raw_[4*cells_+next_]=known?1:0;++next_;
  }
 private:
  friend class Coordinator;
  Writer(char* raw,std::size_t cells,std::shared_ptr<std::atomic<bool>> stopped):raw_(raw),cells_(cells),stopped_(std::move(stopped)){}
  char* raw_;std::size_t cells_,next_=0;std::shared_ptr<std::atomic<bool>> stopped_;
};

class Coordinator {
 public:
  explicit Coordinator(bool enabled=false,Digest digest={},ReceiptVerifier verifier={},Limits limits={})
      :enabled_(enabled),digest_(std::move(digest)),verifier_(std::move(verifier)),limits_(limits) {
    Require(limits_.body_cells>0&&limits_.body_cells<=4194304&&limits_.resident_bytes>0&&limits_.resident_bytes<=20971520&&
        limits_.pending_bodies>0&&limits_.pending_bodies<=8&&limits_.wire_bytes>0&&limits_.wire_bytes<=81920&&
        limits_.chunk_cells>0&&limits_.chunk_cells<=16384&&limits_.catalog_entries>0&&limits_.catalog_entries<=4096&&
        limits_.page_entries>0&&limits_.page_entries<=64&&limits_.stored_refs>0&&limits_.stored_refs<=8192&&
        limits_.trajectory_watermarks>0&&limits_.trajectory_watermarks<=4096,"fixed_research_limits");
    if(enabled_)Require(bool(digest_)&&bool(verifier_),"trusted_callbacks_required");
  }
  State start(Context c) {
    if(!enabled_)return State::OFF;
    ValidateContext(c);
    std::lock_guard<std::mutex> l(mutex_);std::lock_guard<std::mutex> a(account_->mutex);
    if(account_->resident||account_->wire||account_->slots)return State::DEFERRED;
    if(has_context_&&c==context_)return stopped_->load()?State::REJECTED:State::UNCHANGED;
    context_=std::move(c);has_context_=true;stopped_->store(false);fatal_=false;
    pending_.clear();stored_.clear();completed_nonces_.clear();heads_.clear();draft_.reset();
    last_sequence_=last_batch_id_=0;last_RAW_=0;last_local_ticks_.clear();return State::ACCEPTED;
  }
  void stop(){stopped_->store(true);std::lock_guard<std::mutex> l(mutex_);draft_.reset();}
  void fatal_source_fault(){stopped_->store(true);std::lock_guard<std::mutex> l(mutex_);fatal_=true;draft_.reset();}
  // Reserve and synchronously copy under the caller's source lock. No digest,
  // receipt verifier, disk or publisher is called in this stage.
  State capture_raw(BodyRef body,const std::function<void(Writer&)>& fill,Request* result) {
    if(!enabled_)return State::OFF;
    ValidateGeometry(body.geometry,limits_.body_cells);ValidateContext(body.context);
    Require(body.body_id>0&&body.key.first>=0&&body.key.second>=0,"native_identity_required");
    const auto size=5*body.geometry.cells();
    std::string nonce;
    std::uint64_t reservation_order=0;
    {
      std::lock_guard<std::mutex> l(mutex_);
      if(!has_context_||!(body.context==context_))return State::REJECTED;
      if(stopped_->load()||fatal_)return State::STOPPED;
      for(const auto& pending:pending_) {
        const auto& old=pending.second.request.body;
        if(old.key==body.key&&old.finished&&
            (old.version!=body.version||old.body_id!=body.body_id||old.finished!=body.finished||!(old.geometry==body.geometry)))return State::REJECTED;
        if(old.key==body.key&&old.version==body.version&&old.finished==body.finished) {
          if(old.body_id!=body.body_id||!(old.geometry==body.geometry)||(!body.sha256.empty()&&old.sha256!=body.sha256))return State::REJECTED;
          if(result)*result=pending.second.request;
          return State::UNCHANGED;
        }
      }
      const auto h=heads_.find(body.key);
      if(h!=heads_.end()) {
        if(body.version==h->second.version&&body.finished==h->second.finished) {
          if(body.body_id!=h->second.body_id||!(body.geometry==h->second.geometry)||(!body.sha256.empty()&&body.sha256!=h->second.sha256))return State::REJECTED;
          if(result)*result=Request{"",h->second};
          return State::UNCHANGED;
        }
        if(h->second.finished) {
          if(body.version!=h->second.version||body.body_id!=h->second.body_id||body.finished!=h->second.finished||!(body.geometry==h->second.geometry))return State::REJECTED;
          return State::UNCHANGED;
        }
        if(body.version<h->second.version)return State::REJECTED;
      }
      std::lock_guard<std::mutex> a(account_->mutex);
      if(account_->slots>=limits_.pending_bodies||size>limits_.resident_bytes-account_->resident)return State::DEFERRED;
      Require(next_nonce_!=UINT64_MAX,"nonce_counter_overflow");
      reservation_order=++next_nonce_;
      nonce=context_.epoch+":"+std::to_string(reservation_order);
      account_->resident+=size;++account_->slots;
    }
    bool reserved=true;
    try {
      std::unique_ptr<char[]> raw(new char[size]);Writer writer(raw.get(),body.geometry.cells(),stopped_);fill(writer);
      Require(writer.next_==body.geometry.cells(),"partial_native_body");
      Require(body.sha256.empty()||Hex64(body.sha256),"declared_SHA256_syntax");
      Request request{nonce,body};
      std::lock_guard<std::mutex> l(mutex_);
      Require(has_context_&&body.context==context_,"capture_context_changed");
      Require(!stopped_->load()&&!fatal_,"capture_cancelled_by_Stop");
      const auto h=heads_.find(body.key);
      for(const auto& other:pending_) {
        const auto& old=other.second.request.body;
        if(old.key==body.key&&((old.finished&&!(old==body))||(body.finished&&old.version>body.version)))
          throw std::invalid_argument("pending_finished_binding_changed");
      }
      if(h!=heads_.end()&&h->second.finished&&!(body==h->second))throw std::invalid_argument("finished_binding_changed");
      if(h!=heads_.end()&&body.version==h->second.version&&body.finished==h->second.finished&&!(body==h->second))
        throw std::invalid_argument("same_version_binding_changed");
      pending_.emplace(nonce,Resident{request,std::move(raw),size,false,false,reservation_order});reserved=false;
      if(result)*result=request;
      return State::ACCEPTED;
    }catch(...) {
      if(reserved){std::lock_guard<std::mutex> a(account_->mutex);account_->resident-=size;--account_->slots;}
      throw;
    }
  }
  // Caller must invoke this after releasing every Node/source ingress lock.
  // The manager owns/pins raw bytes; no borrowed SDK Grid escapes capture_raw.
  // Stop may finish archival identity, but returns STOPPED and cannot publish.
  State finalize(Request* request) {
    if(!enabled_)return State::OFF;
    Require(request!=nullptr,"finalize_request_required");
    const Request expected=*request;const char* raw=nullptr;std::size_t bytes=0;
    {
      std::lock_guard<std::mutex> l(mutex_);const auto it=pending_.find(expected.nonce);
      if(it==pending_.end()||!(it->second.request==expected)||!(context_==expected.body.context))return State::REJECTED;
      if(it->second.finalized)return State::UNCHANGED;
      if(it->second.finalizing)return State::DEFERRED;
      if(fatal_)return State::STOPPED;
      it->second.finalizing=true;raw=it->second.raw.get();bytes=it->second.size;
    }
    try {
      const auto hash=digest_(std::string_view(raw,bytes));
      Require(Hex64(hash)&&(expected.body.sha256.empty()||expected.body.sha256==hash),"actual_SHA256_required");
      std::lock_guard<std::mutex> l(mutex_);const auto it=pending_.find(expected.nonce);
      Require(it!=pending_.end()&&it->second.request==expected&&context_==expected.body.context,"finalize_context_changed");
      it->second.request.body.sha256=hash;it->second.finalized=true;it->second.finalizing=false;*request=it->second.request;
      return stopped_->load()||fatal_?State::STOPPED:State::ACCEPTED;
    }catch(const std::bad_alloc&) {
      // Allocation pressure is not an integrity fault. Preserve the exact
      // owned bytes/context and release the in-flight finalizer claim only.
      std::lock_guard<std::mutex> l(mutex_);const auto it=pending_.find(expected.nonce);
      if(it!=pending_.end())it->second.finalizing=false;
      return stopped_->load()||fatal_?State::STOPPED:State::DEFERRED;
    }catch(...) {
      std::lock_guard<std::mutex> l(mutex_);const auto it=pending_.find(expected.nonce);
      if(it!=pending_.end())it->second.finalizing=false;
      fatal_=true;stopped_->store(true);draft_.reset();throw;
    }
  }
  State capture(BodyRef body,const std::function<void(Writer&)>& fill,Request* result) {
    Request request;const auto state=capture_raw(std::move(body),fill,&request);
    if(state!=State::ACCEPTED){if(result)*result=request;return state;}
    const auto finalized=finalize(&request);if(result)*result=request;return finalized;
  }
  std::vector<Request> raw_requests() const {
    std::lock_guard<std::mutex> l(mutex_);std::vector<std::pair<std::uint64_t,Request>> ordered;std::vector<Request> result;
    for(const auto& p:pending_)if(!p.second.finalized&&!p.second.finalizing)ordered.emplace_back(p.second.reservation_order,p.second.request);
    std::sort(ordered.begin(),ordered.end(),[](const auto& a,const auto& b){return a.first<b.first;});
    for(const auto& item:ordered)result.push_back(item.second);
    return result;
  }
  std::vector<Request> archive_requests() const {
    std::lock_guard<std::mutex> l(mutex_);std::vector<Request> result;
    for(const auto& p:pending_)if(p.second.finalized)result.push_back(p.second.request);
    return result;
  }
  bool resolve(Request* request) const {
    if(!request)return false;
    std::lock_guard<std::mutex> l(mutex_);const auto it=pending_.find(request->nonce);
    if(it!=pending_.end()) {
      const auto& actual=it->second.request;
      auto expected=*request;expected.body.sha256=actual.body.sha256;
      if(!it->second.finalized||!(expected==actual))return false;
      *request=actual;return true;
    }
    const auto done=completed_nonces_.find(request->nonce);
    if(done==completed_nonces_.end())return false;
    auto expected=*request;expected.body.sha256=done->second.body.sha256;
    if(!(expected==done->second))return false;
    *request=done->second;return true;
  }
  bool owns(const Request& request) const {
    std::lock_guard<std::mutex> l(mutex_);
    if(request.nonce.empty())return Hex64(request.body.sha256)&&stored_.count(Binding(request.body));
    const auto p=pending_.find(request.nonce);
    const Request* actual=p!=pending_.end()?&p->second.request:nullptr;
    const auto done=completed_nonces_.find(request.nonce);
    if(!actual&&done!=completed_nonces_.end())actual=&done->second;
    if(!actual||(!request.body.sha256.empty()&&request.body.sha256!=actual->body.sha256))return false;
    auto expected=request;expected.body.sha256=actual->body.sha256;return expected==*actual;
  }
  bool ack(const Ack& ack) {
    if(!enabled_)return false;
    if(ack.receipt.empty()||ack.receipt.size()>128)return false;
    Request expected;
    {
      std::lock_guard<std::mutex> l(mutex_);const auto it=pending_.find(ack.request.nonce);
      if(it==pending_.end()||!it->second.finalized||!(it->second.request==ack.request))return false;
      expected=it->second.request;
      if(stored_.size()>=limits_.stored_refs&&!stored_.count(Binding(expected.body)))return false;
      if(completed_nonces_.size()>=limits_.stored_refs&&!completed_nonces_.count(expected.nonce))return false;
    }
    // Verifier must not retain or borrow body RAM: it receives metadata only.
    // ACK may commit while stopped; it may never publish a new catalog.
    if(!verifier_(ack))return false;
    std::lock_guard<std::mutex> l(mutex_);const auto it=pending_.find(expected.nonce);
    if(it==pending_.end()||!(it->second.request==expected)||!(context_==expected.body.context))return false;
    const auto binding=Binding(expected.body);
    if(stored_.size()>=limits_.stored_refs&&!stored_.count(binding))return false;
    if(completed_nonces_.size()>=limits_.stored_refs&&!completed_nonces_.count(expected.nonce))return false;
    stored_[binding]=expected.body;
    completed_nonces_[expected.nonce]=expected;
    const auto head=heads_.find(expected.body.key);
    if(head==heads_.end()||expected.body.version>head->second.version||
        (expected.body.version==head->second.version&&expected.body.finished))heads_[expected.body.key]=expected.body;
    std::lock_guard<std::mutex> a(account_->mutex);
    const auto released=it->second.size;pending_.erase(it);
    account_->resident-=released;--account_->slots;return true;
  }
  WireLease chunk(const Request& request,std::size_t offset,std::size_t cells) {
    Require(enabled_,"OFF");std::lock_guard<std::mutex> l(mutex_);
    const auto it=pending_.find(request.nonce);Require(it!=pending_.end()&&it->second.finalized&&it->second.request==request,"exact_finalized_resident_request");
    const auto total=request.body.geometry.cells();Require(cells>0&&cells<=limits_.chunk_cells&&offset<=total&&cells<=total-offset,"bounded_wire_chunk");
    const auto bytes=5*cells;std::lock_guard<std::mutex> a(account_->mutex);
    Require(bytes<=limits_.wire_bytes-account_->wire,"wire_copy_budget");account_->wire+=bytes;
    try {
      std::unique_ptr<char[]> output(new char[bytes]);
      std::memcpy(output.get(),it->second.raw.get()+4*offset,4*cells);
      std::memcpy(output.get()+4*cells,it->second.raw.get()+4*total+offset,cells);
      return WireLease(account_,std::move(output),bytes);
    }catch(...){account_->wire-=bytes;throw;}
  }
  State draft(Frame frame) {
    if(!enabled_)return State::OFF;
    std::lock_guard<std::mutex> l(mutex_);
    if(stopped_->load()||fatal_)return State::STOPPED;
    if(!(frame.context==context_)||!has_context_)return State::REJECTED;
    if(frame.entries.size()>limits_.catalog_entries||
        frame.observation.latest_local_slam_ticks.size()>limits_.trajectory_watermarks)return State::DEFERRED;
    if(frame.sequence<=last_sequence_)return State::REJECTED;
    if(frame.batch_id<last_batch_id_) {
      fatal_=true;stopped_->store(true);draft_.reset();return State::REJECTED;
    }
    if(frame.observation.captured_RAW_ns<=last_RAW_||frame.observation.captured_ROS_ns<0) {
      fatal_=true;stopped_->store(true);draft_.reset();return State::REJECTED;
    }
    std::size_t new_watermarks=0;
    for(const auto& stamp:frame.observation.latest_local_slam_ticks) {
      const auto old=last_local_ticks_.find(stamp.first);
      if(stamp.second<0||(old!=last_local_ticks_.end()&&stamp.second<old->second)) {
        fatal_=true;stopped_->store(true);draft_.reset();return State::REJECTED;
      }
      if(old==last_local_ticks_.end())++new_watermarks;
    }
    if(new_watermarks>limits_.trajectory_watermarks-last_local_ticks_.size())return State::DEFERRED;
    std::set<Key> keys;Key previous{-1,-1};
    for(const auto& e:frame.entries) {
      ValidateGeometry(e.body.geometry,limits_.body_cells);
      Require(e.body.context==context_&&Hex64(e.body.sha256)&&e.body.body_id>0&&
          e.body.key.first>=0&&e.body.key.second>=0&&e.body.key>previous,"ordered_full_context_body_refs");
      previous=e.body.key;keys.insert(e.body.key);
      for(double v:e.global_pose)Require(std::isfinite(v),"nonfinite_global_pose");
      const auto h=heads_.find(e.body.key);
      if(h!=heads_.end()&&h->second.finished&&!(e.body==h->second))return State::REJECTED;
    }
    if(latest_&&latest_->context==context_)for(const auto& e:latest_->entries)if(!keys.count(e.body.key))return State::REJECTED;
    // Prepare all allocating metadata before committing the clock/sequence
    // floors. A recoverable allocation failure must allow the exact retry.
    auto next_local_ticks=last_local_ticks_;
    for(const auto& stamp:frame.observation.latest_local_slam_ticks)next_local_ticks[stamp.first]=stamp.second;
    auto admitted=std::make_unique<Frame>(std::move(frame));
    last_sequence_=admitted->sequence;last_batch_id_=admitted->batch_id;
    last_RAW_=admitted->observation.captured_RAW_ns;
    // Missing trajectories retain their bounded source-context clock floors.
    last_local_ticks_.swap(next_local_ticks);draft_=std::move(admitted);return State::ACCEPTED;
  }
  State publish() {
    if(!enabled_)return State::OFF;
    std::lock_guard<std::mutex> l(mutex_);
    if(stopped_->load()||fatal_)return State::STOPPED;
    if(!draft_)return State::DEFERRED;
    for(const auto& e:draft_->entries)if(!stored_.count(Binding(e.body)))return State::DEFERRED;
    // Pointer commit shares the exact Stop fence; no blocking IO/publisher here.
    previous_=std::move(latest_);latest_=std::move(draft_);
    last_sequence_=latest_->sequence;last_batch_id_=latest_->batch_id;
    last_RAW_=latest_->observation.captured_RAW_ns;
    return State::ACCEPTED;
  }
  Page page(const Context& expected,std::uint64_t sequence,std::uint64_t batch_id,std::size_t ordinal) const {
    std::lock_guard<std::mutex> l(mutex_);const Frame* frame=nullptr;
    if(latest_&&latest_->context==expected&&latest_->sequence==sequence&&latest_->batch_id==batch_id)frame=latest_.get();
    else if(previous_&&previous_->context==expected&&previous_->sequence==sequence&&previous_->batch_id==batch_id)frame=previous_.get();
    Require(frame!=nullptr,"explicit_catalog_gone_or_identity_mismatch");
    const auto count=frame->entries.size();Require(ordinal<=(count?((count-1)/limits_.page_entries):0),"bounded_page_ordinal");
    const auto first=ordinal*limits_.page_entries,last=std::min(count,first+limits_.page_entries);
    return Page{frame->context,sequence,batch_id,count,ordinal,std::vector<Entry>(frame->entries.begin()+first,frame->entries.begin()+last)};
  }
  std::array<std::size_t,3> accounting() const {std::lock_guard<std::mutex> a(account_->mutex);return {{account_->resident,account_->wire,account_->slots}};}
  bool stopped() const{return stopped_->load();}
  CoordinatorView view() const {
    std::lock_guard<std::mutex> l(mutex_);std::lock_guard<std::mutex> a(account_->mutex);
    CoordinatorView v;v.enabled=enabled_;v.stopped=stopped_->load();v.fatal=fatal_;v.context=context_;
    if(latest_)v.published=CatalogIdentity{latest_->context,latest_->sequence,latest_->batch_id};
    v.resident_bytes=account_->resident;v.wire_bytes=account_->wire;v.pending_requests=account_->slots;
    for(const auto& p:pending_) {
      if(p.second.finalized)++v.archive_requests;
      else if(p.second.finalizing)++v.finalizing_requests;
      else ++v.raw_requests;
    }
    return v;
  }
  State close(){stop();const auto used=accounting();return (used[0]||used[1]||used[2])?State::DEFERRED:State::ACCEPTED;}
 private:
  struct Resident {Request request;std::unique_ptr<char[]> raw;std::size_t size;bool finalized,finalizing;std::uint64_t reservation_order;};
  bool enabled_,has_context_=false,fatal_=false;Digest digest_;ReceiptVerifier verifier_;Limits limits_;Context context_;
  mutable std::mutex mutex_;std::shared_ptr<Accounting> account_=std::make_shared<Accounting>();
  std::shared_ptr<std::atomic<bool>> stopped_=std::make_shared<std::atomic<bool>>(true);
  std::uint64_t next_nonce_=0,last_sequence_=0,last_batch_id_=0;std::int64_t last_RAW_=0;
  std::map<std::int32_t,std::int64_t> last_local_ticks_;
  std::map<std::string,Resident> pending_;std::map<std::string,BodyRef> stored_;std::map<Key,BodyRef> heads_;
  std::map<std::string,Request> completed_nonces_; // bounded by stored_refs; metadata only
  std::unique_ptr<Frame> draft_,latest_,previous_;
};
}}} // ubts::research::derived_catalog
#endif
