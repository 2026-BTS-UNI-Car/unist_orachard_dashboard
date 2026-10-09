// Original-CDR Iron stream until explicit Stop. No publisher/control/map/device IO.
// Derived from finite observer dd3c15374ad6981db31d4949e624cbaa4271d0edb205210409b8c54bdd0ad463.
// Pipe backpressure can block stdout: the external exact-owner must enforce termination.
// Source-only derivative; the finite probe qualification does not qualify this stream.
#include <rclcpp/rclcpp.hpp>
#include <rclcpp/generic_subscription.hpp>
#include <rclcpp/message_info.hpp>
#include <rclcpp/typesupport_helpers.hpp>
#include <rmw/rmw.h>
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <array>
#include <chrono>
#include <cstdint>
#include <csignal>
#include <limits>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <time.h>
#include <unistd.h>

static_assert(RMW_GID_STORAGE_SIZE == 16u, "Actual Iron GID must be exactly16 bytes");
namespace {
using Clock=std::chrono::steady_clock;
constexpr std::size_t MAX_CDR=256u*1024u, MAX_LINE=2u*MAX_CDR+8192u;
constexpr uint64_t COUNTER_MAX=uint64_t(INT64_MAX), FINAL_RESERVE=8192u;
constexpr int READY_SECONDS=15;
volatile std::sig_atomic_t stop_requested=0;
void stop_signal(int signal) noexcept {stop_requested=signal;}
constexpr const char * SCAN="/scan", * IMU="/imu/data_raw";
constexpr const char * SCAN_TYPE="sensor_msgs/msg/LaserScan", * IMU_TYPE="sensor_msgs/msg/Imu";
void require(bool v,const char * reason){if(!v)throw std::runtime_error(reason);}
void install_stop_handlers(){
  struct sigaction stop{};stop.sa_handler=stop_signal;stop.sa_flags=0;
  require(sigemptyset(&stop.sa_mask)==0&&sigaction(SIGINT,&stop,nullptr)==0&&
    sigaction(SIGTERM,&stop,nullptr)==0,"stop_signal_handlers_failed");
  struct sigaction pipe{};pipe.sa_handler=SIG_IGN;pipe.sa_flags=0;
  require(sigemptyset(&pipe.sa_mask)==0&&sigaction(SIGPIPE,&pipe,nullptr)==0,"stdout_SIGPIPE_handler_failed");
}
int64_t checked_difference(int64_t newer,int64_t older){
  // Both operands nonnegative signed63: subtraction cannot overflow int64.
  require(newer>=0&&older>=0,"negative_clock_for_difference");return newer-older;
}
std::string hex(const uint8_t * p,std::size_t n){
  std::string out;out.resize(n*2);constexpr char digits[]="0123456789abcdef";
  for(std::size_t i=0;i<n;++i){out[2*i]=digits[p[i]>>4];out[2*i+1]=digits[p[i]&15];}return out;
}
std::string json_string(const std::string & value){
  std::ostringstream s;s<<'"';
  for(unsigned char c:value){if(c=='"'||c=='\\')s<<'\\'<<c;
    else if(c<32||c>=127)s<<"\\u00"<<std::hex<<std::setw(2)<<std::setfill('0')<<unsigned(c);else s<<c;}
  s<<'"';return s.str();
}
std::string sha(const uint8_t * p,std::size_t n){
  std::array<uint8_t,EVP_MAX_MD_SIZE> digest{};unsigned size=0;
  require(EVP_Digest(p,n,digest.data(),&size,EVP_sha256(),nullptr)==1&&size==32,"SHA256_failed");
  return hex(digest.data(),size);
}
int64_t clock_ns(clockid_t id){timespec t{};require(clock_gettime(id,&t)==0&&t.tv_sec>=0&&t.tv_nsec>=0&&t.tv_nsec<1000000000,"clock_read_failed");
  require(uint64_t(t.tv_sec)<=(uint64_t(INT64_MAX)-uint64_t(t.tv_nsec))/1000000000u,"clock_overflow");
  return int64_t(t.tv_sec)*1000000000ll+int64_t(t.tv_nsec);}
std::string read_small(const char * path,std::size_t cap){
  std::ifstream f(path,std::ios::binary);require(f.good(),"readonly_provenance_open_failed");
  std::string text(cap+1,'\0');f.read(text.data(),static_cast<std::streamsize>(cap+1));text.resize(std::size_t(f.gcount()));
  require(text.size()<=cap&&f.eof(),"bounded_provenance_required");return text;
}
std::string raw_domain(){
  std::string boot=read_small("/proc/sys/kernel/random/boot_id",64);
  while(!boot.empty()&&(boot.back()=='\n'||boot.back()=='\r'))boot.pop_back();
  require(boot.size()==36,"boot_identity_required");
  for(std::size_t i=0;i<boot.size();++i){bool hyphen=i==8||i==13||i==18||i==23;
    require(hyphen?boot[i]=='-':((boot[i]>='0'&&boot[i]<='9')||(boot[i]>='a'&&boot[i]<='f')),"boot_identity_invalid");}
  boot+=":CLOCK_MONOTONIC_RAW";return sha(reinterpret_cast<const uint8_t*>(boot.data()),boot.size());
}
std::string time_namespace(){std::array<char,128> text{};ssize_t n=readlink("/proc/self/ns/time",text.data(),text.size());
  require(n>0&&std::size_t(n)<text.size(),"time_namespace_identity_required");return std::string(text.data(),std::size_t(n));}
bool gid_string(const std::string & value){if(value.size()!=32)return false;bool nonzero=false;
  for(char c:value){if(!((c>='0'&&c<='9')||(c>='a'&&c<='f')))return false;nonzero|=c!='0';}return nonzero;}
struct Header {int32_t sec;uint32_t nanosec;std::string frame;std::size_t beam_count=0,intensity_count=0;int64_t ns()const{return int64_t(sec)*1000000000ll+nanosec;}};
struct Reader {
  const uint8_t * bytes;std::size_t size,at=4;bool little;
  Reader(const uint8_t * b,std::size_t n):bytes(b),size(n),little(b&&n>=2&&b[1]==1){
    require(b&&n>=4&&n<=MAX_CDR,"bounded_original_CDR_required");
    require(b[0]==0&&(b[1]==0||b[1]==1)&&b[2]==0&&b[3]==0,"unsupported_CDR1_encapsulation");}
  const uint8_t * take(std::size_t count,std::size_t align=1){
    std::size_t pad=(align-(at-4)%align)%align;require(at<=size&&pad<=size-at&&count<=size-at-pad,"truncated_original_CDR");
    at+=pad;const auto * p=bytes+at;at+=count;return p;}
  uint32_t u32(){const auto * p=take(4,4);return little?uint32_t(p[0])|(uint32_t(p[1])<<8)|(uint32_t(p[2])<<16)|(uint32_t(p[3])<<24):
    uint32_t(p[3])|(uint32_t(p[2])<<8)|(uint32_t(p[1])<<16)|(uint32_t(p[0])<<24);}
  Header parse(bool scan){uint32_t s=u32(),ns=u32();int32_t sec;std::memcpy(&sec,&s,4);
    require(sec>=0&&ns<1000000000u,"original_header_stamp_invalid");
    uint32_t length=u32();require(length>=2&&length<=129,"bounded_original_frame_required");const auto * f=take(length);
    require(f[length-1]==0&&std::memchr(f,0,length-1)==nullptr,"original_frame_string_invalid");
    Header header{sec,ns,std::string(reinterpret_cast<const char*>(f),length-1),0,0};
    if(scan){take(7*4,4);uint32_t beams=u32();require(beams>=24&&beams<=4096,"full_native_beam_count_unsupported");take(std::size_t(beams)*4,4);
      uint32_t intensities=u32();require(intensities==0||intensities==beams,"original_intensity_count_mismatch");take(std::size_t(intensities)*4,4);
      header.beam_count=beams;header.intensity_count=intensities;
    }else{for(std::size_t count:{4u,9u,3u,9u,3u,9u})take(count*8,8);}
    require(at==size,"unsupported_original_CDR_trailing_bytes");return header;}
};
struct Output {
  uint64_t seq=0,bytes=0,samples=0;
  void emit(const std::string & body,bool sample=false,bool final=false){
    require(seq<COUNTER_MAX-(final?0u:1u),"sequence_counter_capacity");
    require(!sample||samples<COUNTER_MAX,"sample_counter_capacity");
    const std::string line="{\"seq\":"+std::to_string(seq)+","+body+"}\n";
    const uint64_t reserve=final?0u:FINAL_RESERVE;
    require(line.size()<=MAX_LINE&&bytes<=COUNTER_MAX-reserve&&
      uint64_t(line.size())<=COUNTER_MAX-reserve-bytes,"NDJSON_capacity");
    std::cout<<line<<std::flush;require(std::cout.good(),"stdout_delivery_failed");
    ++seq;bytes+=uint64_t(line.size());if(sample)++samples;
  }
};
// Iron's generic callback omits MessageInfo. Intercept the executor hook before
// GenericSubscription's base callback discards it; never reserialize a message.
class OriginalSubscription final:public rclcpp::GenericSubscription {
 public:
  using Received=std::function<void(const rclcpp::SerializedMessage &,const rclcpp::MessageInfo &,int64_t,int64_t)>;
  OriginalSubscription(rclcpp::node_interfaces::NodeBaseInterface * base,
    const std::shared_ptr<rcpputils::SharedLibrary> & library,const std::string & topic,const std::string & type,
    const rclcpp::QoS & qos,const rclcpp::SubscriptionOptions & options,Received received,std::string & fault)
  :GenericSubscription(base,library,topic,type,qos,[](std::shared_ptr<rclcpp::SerializedMessage>){},options),
    received_(std::move(received)),fault_(fault){}
  void handle_serialized_message(const std::shared_ptr<rclcpp::SerializedMessage> & message,const rclcpp::MessageInfo & info)override{
    try{const int64_t raw=clock_ns(CLOCK_MONOTONIC_RAW);const int64_t wall=clock_ns(CLOCK_REALTIME);
      require(message&&fault_.empty(),"null_serialized_message_or_previous_fault");received_(*message,info,raw,wall);
    }catch(const std::exception & e){if(fault_.empty())fault_=std::string(e.what()).substr(0,256);}
  }
  void handle_message(std::shared_ptr<void> &,const rclcpp::MessageInfo &)override{fault_="unexpected_nonserialized_delivery";}
 private:Received received_;std::string & fault_;
};
rclcpp::NodeOptions options(){rclcpp::NodeOptions value;
  value.enable_rosout(false).enable_logger_service(false).start_parameter_services(false).start_parameter_event_publisher(false)
    .enable_topic_statistics(false).use_global_arguments(false).use_intra_process_comms(false).use_clock_thread(false)
    .clock_type(RCL_SYSTEM_TIME).append_parameter_override("use_sim_time",false);return value;}
class Observer final:public rclcpp::Node {
 public:
  Observer(Output & output,std::string scan_gid,std::string imu_gid,std::string rmw,std::string domain)
  :Node("ubts_private_actual_cdr_"+std::to_string(getpid()),options()),output_(output),scan_gid_(std::move(scan_gid)),
    imu_gid_(std::move(imu_gid)),rmw_(std::move(rmw)),domain_(std::move(domain)){
    rclcpp::QoS qos(rclcpp::KeepLast(8));qos.best_effort().durability_volatile();rclcpp::SubscriptionOptions sub;
    sub.use_default_callbacks=false;
    for(bool scan:{true,false}){const char * topic=scan?SCAN:IMU,*type=scan?SCAN_TYPE:IMU_TYPE;
      auto library=rclcpp::get_typesupport_library(type,"rosidl_typesupport_cpp");
      auto subscription=std::make_shared<OriginalSubscription>(get_node_base_interface().get(),library,topic,type,qos,sub,
        [this,scan](const rclcpp::SerializedMessage & m,const rclcpp::MessageInfo & i,int64_t raw,int64_t wall){receive(scan,m,i,raw,wall);},fault);
      get_node_topics_interface()->add_subscription(subscription,sub.callback_group);subscriptions_[scan?0:1]=subscription;}
  }
  std::string fault;std::array<uint64_t,2> counts{{0,0}};std::array<int64_t,2> last{{-1,-1}};
  std::array<std::string,2> graph_nodes,graph_namespaces;
  Clock::time_point deadline=Clock::time_point::max();
  void close_subscriptions(){for(auto & subscription:subscriptions_)subscription.reset();}
  bool graph_matches(){return graph_one(SCAN,SCAN_TYPE,scan_gid_)&&graph_one(IMU,IMU_TYPE,imu_gid_);}
 private:
  bool graph_one(const char * topic,const char * type,const std::string & gid){auto values=get_publishers_info_by_topic(topic);
    require(values.size()<=1,"multiple_actual_publishers");if(values.empty())return false;const auto & v=values.front();
    if(v.node_name()=="_NODE_NAME_UNKNOWN_"||v.node_namespace()=="_NODE_NAMESPACE_UNKNOWN_")return false;
    require(!v.node_name().empty()&&!v.node_namespace().empty()&&v.topic_type()==type,"actual_graph_node_or_type_invalid");
    auto endpoint=v.endpoint_gid();require(endpoint.size()==16&&hex(endpoint.data(),endpoint.size())==gid,"actual_graph_GID_changed");
    const std::size_t index=std::strcmp(topic,SCAN)==0?0:1;graph_nodes[index]=v.node_name();graph_namespaces[index]=v.node_namespace();return true;}
  void receive(bool scan,const rclcpp::SerializedMessage & message,const rclcpp::MessageInfo & info,int64_t raw,int64_t wall){
    if(stop_requested||Clock::now()>=deadline)return;const auto & data=message.get_rcl_serialized_message();
    require(data.buffer&&data.buffer_length>=4&&data.buffer_length<=MAX_CDR,"original_CDR_capacity");
    const auto & details=info.get_rmw_message_info();const auto & gid=details.publisher_gid;
    require(gid.implementation_identifier&&rmw_==gid.implementation_identifier,"actual_callback_RMW_missing_or_changed");
    const std::string actual=hex(gid.data,RMW_GID_STORAGE_SIZE),expected=scan?scan_gid_:imu_gid_;
    require(actual==expected,"actual_callback_GID_missing_or_changed");
    Reader reader(data.buffer,data.buffer_length);const Header h=reader.parse(scan);std::size_t index=scan?0:1;
    require(h.ns()>=last[index],"original_sensor_header_rewind");
    const int64_t ros_now=get_clock()->now().nanoseconds(),age=checked_difference(wall,h.ns());
    require(ros_now>=0,"observer_system_ros_clock_negative");
    require(counts[index]<COUNTER_MAX,"topic_sample_counter_capacity");
    require(get_clock()->get_clock_type()==RCL_SYSTEM_TIME,"observer_independent_system_clock_required");
    output_.emit("\"kind\":\"original_cdr\",\"topic\":"+json_string(scan?SCAN:IMU)+",\"ros_type\":"+json_string(scan?SCAN_TYPE:IMU_TYPE)+
      ",\"callback_monotonic_raw_ns\":"+std::to_string(raw)+",\"RAW_schema\":\"linux.clock_monotonic_raw.integer_ns.v1\",\"RAW_domain\":"+json_string(domain_)+
      ",\"receipt_clock_realtime_ns\":"+std::to_string(wall)+",\"observer_system_ros_now_ns\":"+std::to_string(ros_now)+
      ",\"original_header_stamp_sec\":"+std::to_string(h.sec)+",\"original_header_stamp_nanosec\":"+std::to_string(h.nanosec)+
      ",\"original_header_stamp_ns\":"+std::to_string(h.ns())+",\"frame_id_bytes_hex\":"+json_string(hex(reinterpret_cast<const uint8_t*>(h.frame.data()),h.frame.size()))+
      ",\"ranges_count\":"+std::to_string(h.beam_count)+",\"intensities_count\":"+std::to_string(h.intensity_count)+
      ",\"publisher_gid_hex\":"+json_string(actual)+",\"rmw_source_timestamp_ns\":"+std::to_string(details.source_timestamp)+
      ",\"rmw_received_timestamp_ns\":"+std::to_string(details.received_timestamp)+",\"original_CDR_bytes\":"+std::to_string(data.buffer_length)+
      ",\"original_CDR_sha256\":"+json_string(sha(data.buffer,data.buffer_length))+",\"original_CDR_hex\":"+json_string(hex(data.buffer,data.buffer_length))+
      ",\"header_to_receipt_system_age_ns\":"+std::to_string(age)+",\"header_system_time_compatible\":"+(age>=0&&age<=5000000000ll?"true":"false")+
      ",\"mission_admission\":false,\"proposed_linear_mps\":0,\"proposed_yaw_rate_rad_s\":0",true);
    last[index]=h.ns();++counts[index];require(age>=0&&age<=5000000000ll,"original_header_not_current_system_time");
  }
  Output & output_;std::string scan_gid_,imu_gid_,rmw_,domain_;
  std::array<std::shared_ptr<OriginalSubscription>,2> subscriptions_;
};
} // namespace

int main(int argc,char ** argv){
  if(argc==1){std::cout<<"{\"mode\":\"plan\",\"streaming\":true,\"ROS_initialized\":false,\"startup_readiness_seconds\":15,\"until_operator_stop\":true,\"CDR_bytes_max\":262144,\"line_bytes_max\":532480,\"queue_depth_per_topic\":8,\"publishers_or_map_or_control\":false}\n";return 0;}
  Output output;bool initialized=false,ready=false,destroyed=false,subscriptions_destroyed=false,
    executor_destroyed=false,shutdown=false;
  std::string error,domain,rmw;std::shared_ptr<Observer> node;
  std::unique_ptr<rclcpp::executors::SingleThreadedExecutor> executor;
  std::array<uint64_t,2> counts{{0,0}};int64_t started_raw=0,elapsed=0;
  try{
    require(argc==6&&std::strcmp(argv[1],"--observe-until-stop")==0&&std::strcmp(argv[2],"--expected-scan-gid")==0&&
      std::strcmp(argv[4],"--expected-imu-gid")==0&&gid_string(argv[3])&&gid_string(argv[5])&&std::strcmp(argv[3],argv[5])!=0,"fixed_explicit_stream_CLI_required");
    const char * distro=std::getenv("ROS_DISTRO"),* ros_domain=std::getenv("ROS_DOMAIN_ID");
    require(distro&&std::strcmp(distro,"iron")==0&&ros_domain&&std::strcmp(ros_domain,"0")==0,"actual_Iron_domain0_environment_required");
    install_stop_handlers();domain=raw_domain();const std::string time_ns=time_namespace();std::array<uint8_t,16> random{};
    require(RAND_bytes(random.data(),random.size())==1,"private_observer_session_failed");const auto session=hex(random.data(),random.size());
    clock_ns(CLOCK_MONOTONIC_RAW);
    rclcpp::init(0,nullptr,rclcpp::InitOptions(),rclcpp::SignalHandlerOptions::None);initialized=true;
    const char * implementation=rmw_get_implementation_identifier();
    require(implementation&&std::strlen(implementation)<=128,"actual_RMW_identifier_required");rmw=implementation;
    node=std::make_shared<Observer>(output,argv[3],argv[5],rmw,domain);
    executor=std::make_unique<rclcpp::executors::SingleThreadedExecutor>();executor->add_node(node);
    const auto started=Clock::now();started_raw=clock_ns(CLOCK_MONOTONIC_RAW);
    require(started<=Clock::time_point::max()-std::chrono::seconds(READY_SECONDS),"startup_deadline_overflow");
    const auto readiness_deadline=started+std::chrono::seconds(READY_SECONDS);
    node->deadline=Clock::time_point::max();
    output.emit("\"kind\":\"begin\",\"schema\":\"ubts.private.actual_native_cdr_observer.v1\",\"streaming\":true,\"observer_process_session\":"+json_string(session)+
      ",\"pid\":"+std::to_string(getpid())+",\"uid\":"+std::to_string(geteuid())+",\"gid\":"+std::to_string(getegid())+
      ",\"time_namespace\":"+json_string(time_ns)+",\"RAW_domain\":"+json_string(domain)+",\"rmw_implementation\":"+json_string(rmw)+
      ",\"GID_storage_bytes\":16,\"expected_scan_GID\":"+json_string(argv[3])+",\"expected_IMU_GID\":"+json_string(argv[5])+
      ",\"header_clock_origin\":\"original_publisher_unmodified\",\"observer_clock_type\":\"RCL_SYSTEM_TIME\",\"use_sim_time\":false,\"clock_topic_created\":false,\"publishers_created\":false,\"mission_admission\":false");
    auto candidate=Clock::time_point{};auto next_graph=started;
    while(!stop_requested){
      require(rclcpp::ok(),"ROS_context_stopped");executor->spin_some(std::chrono::milliseconds(10));
      require(node->fault.empty(),node->fault.c_str());if(stop_requested)break;
      const auto now=Clock::now();
      if(now>=next_graph){
        next_graph=now+std::chrono::milliseconds(100);
        if(node->graph_matches()){
          if(candidate==Clock::time_point{})candidate=now;
          if(!ready&&now-candidate>=std::chrono::milliseconds(50)&&node->counts[0]>=2&&node->counts[1]>=2){
            require(now<readiness_deadline,"original_CDR_callback_readiness_not_observed_within15s");ready=true;
            output.emit("\"kind\":\"ready\",\"actual_original_CDR_and_per_message_GIDs\":true,\"stable_graph_min_seconds\":0.05,\"scan_node\":"+
              json_string(node->graph_nodes[0])+",\"scan_namespace\":"+json_string(node->graph_namespaces[0])+",\"IMU_node\":"+
              json_string(node->graph_nodes[1])+",\"IMU_namespace\":"+json_string(node->graph_namespaces[1])+",\"mission_admission\":false");}
        }else{candidate=Clock::time_point{};require(!ready,"actual_graph_disappeared_after_readiness");}
      }
      require(ready||now<readiness_deadline,"original_CDR_callback_readiness_not_observed_within15s");usleep(1000);
    }
    require(node->fault.empty(),node->fault.c_str());
  }catch(const std::exception & e){error=std::string(e.what()).substr(0,512);}
  auto cleanup_error=[&error](const std::exception & e){if(error.empty())error=std::string(e.what()).substr(0,512);};
  if(executor){
    try{executor->cancel();if(node)executor->remove_node(node);}catch(const std::exception & e){cleanup_error(e);}
    executor.reset();executor_destroyed=true;
  }
  if(node){counts=node->counts;node->close_subscriptions();subscriptions_destroyed=true;node.reset();destroyed=true;}
  if(initialized){try{shutdown=rclcpp::shutdown();}catch(const std::exception & e){cleanup_error(e);}}
  try{if(started_raw)elapsed=checked_difference(clock_ns(CLOCK_MONOTONIC_RAW),started_raw);
    require(elapsed>=0,"observation_RAW_clock_regressed");}catch(const std::exception & e){cleanup_error(e);}
  // Cancellation before READY may prove clean shutdown, never sensor readiness.
  const bool ok=initialized&&executor_destroyed&&subscriptions_destroyed&&destroyed&&shutdown&&stop_requested&&error.empty();
  try{output.emit("\"kind\":\"done\",\"schema\":\"ubts.private.actual_native_cdr_observer.v1\",\"streaming\":true,\"ok\":"+std::string(ok?"true":"false")+
    ",\"termination_reason\":"+json_string(error.empty()&&stop_requested?"operator_stop":"fault")+
    ",\"ready_observed\":"+(ready?"true":"false")+",\"error\":"+json_string(error)+
    ",\"scan_samples\":"+std::to_string(counts[0])+",\"IMU_samples\":"+std::to_string(counts[1])+
    ",\"ROS_executor_destroyed\":"+(executor_destroyed?"true":"false")+
    ",\"ROS_subscriptions_destroyed\":"+(subscriptions_destroyed?"true":"false")+
    ",\"ROS_node_destroyed\":"+(destroyed?"true":"false")+",\"ROS_shutdown_completed\":"+(shutdown?"true":"false")+
    ",\"observed_elapsed_ns\":"+std::to_string(elapsed)+",\"observed_elapsed_clock\":\"CLOCK_MONOTONIC_RAW\""+
    ",\"mission_admission\":false,\"physical_autonomy_qualified\":false",false,true);
  }catch(const std::exception & e){std::cerr<<e.what()<<'\n';return 1;}return ok?0:1;
}
