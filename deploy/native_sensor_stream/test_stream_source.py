"""Source-contract checks only; no C++ build, ROS, stream or device qualification."""
import hashlib
from pathlib import Path
import re
import unittest

HERE = Path(__file__).resolve().parent
CPP = HERE / "observer-src" / "actual_cdr_stream.cpp"
FINITE = HERE.parent / "native_sensor_shadow" / "observer-src" / "actual_cdr_observer.cpp"
FINITE_SHA = "dd3c15374ad6981db31d4949e624cbaa4271d0edb205210409b8c54bdd0ad463"


def section(text, start, end):
    return text.split(start, 1)[1].split(end, 1)[0]


class StreamSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = CPP.read_text(encoding="utf-8")
        cls.finite = FINITE.read_text(encoding="utf-8")
        cls.main = cls.source.split("int main(", 1)[1]

    def test_original_finite_source_unchanged(self):
        self.assertEqual(hashlib.sha256(FINITE.read_bytes()).hexdigest(), FINITE_SHA)
        self.assertIn(FINITE_SHA, self.source)

    def test_original_cdr_parser_and_exact_receipt_hook_preserved(self):
        for begin, end in [
            ("struct Header {", "struct Output {"),
            ("class OriginalSubscription final", "rclcpp::NodeOptions options()"),
            ("rclcpp::NodeOptions options()", "class Observer final"),
        ]:
            self.assertEqual(section(self.source, begin, end), section(self.finite, begin, end))
        self.assertNotIn("serialize_message(", self.source)
        self.assertNotIn("deserialize_message(", self.source)

    def test_original_sample_payload_and_header_age_gate_preserved(self):
        self.assertEqual(
            section(self.source, "    output_.emit(", ",true);"),
            section(self.finite, "    output_.emit(", ",true);"),
        )
        self.assertIn('age=checked_difference(wall,h.ns())', self.source)
        self.assertIn('require(age>=0&&age<=5000000000ll,"original_header_not_current_system_time")', self.source)
        self.assertIn('original_sensor_header_rewind', self.source)
        self.assertIn('actual_callback_GID_missing_or_changed', self.source)
        self.assertLess(self.source.index('actual==expected'), self.source.index('Reader reader(data.buffer'))

    def test_default_returns_before_ros_or_signal_registration(self):
        default = self.main.split("  Output output;", 1)[0]
        self.assertIn('if(argc==1)', default)
        self.assertIn('return 0;', default)
        self.assertIn('\\"ROS_initialized\\":false', default)
        for action in ('rclcpp::init(', 'install_stop_handlers()', 'make_shared<Observer>', 'spin_some('):
            self.assertNotIn(action, default)
        cli = self.main.index('fixed_explicit_stream_CLI_required')
        self.assertLess(cli, self.main.index('install_stop_handlers();'))
        self.assertLess(cli, self.main.index('rclcpp::init('))
        self.assertIn('argc==6', self.main)
        self.assertIn('"--observe-until-stop"', self.main)
        self.assertIn('gid_string(argv[3])&&gid_string(argv[5])&&std::strcmp(argv[3],argv[5])!=0', self.main)

    def test_signal_handler_only_sets_atomic_flag_and_ros_has_no_signal_handler(self):
        self.assertIn('volatile std::sig_atomic_t stop_requested=0;', self.source)
        body = section(self.source, 'void stop_signal(int signal) noexcept {', '}')
        self.assertEqual(body, 'stop_requested=signal;')
        self.assertIn('sigaction(SIGINT,&stop,nullptr)', self.source)
        self.assertIn('sigaction(SIGTERM,&stop,nullptr)', self.source)
        self.assertIn('pipe.sa_handler=SIG_IGN', self.source)
        self.assertIn('sigaction(SIGPIPE,&pipe,nullptr)', self.source)
        self.assertIn('rclcpp::SignalHandlerOptions::None', self.main)
        self.assertIn('if(stop_requested||Clock::now()>=deadline)return;', self.source)

    def test_lifetime_is_stop_driven_but_startup_and_queue_bounds_remain(self):
        self.assertIn('while(!stop_requested)', self.main)
        self.assertIn('Clock::time_point deadline=Clock::time_point::max();', self.source)
        self.assertIn('node->deadline=Clock::time_point::max();', self.main)
        self.assertIn('constexpr int READY_SECONDS=15;', self.source)
        self.assertIn('ready||now<readiness_deadline', self.main)
        self.assertIn('require(now<readiness_deadline', self.main)
        self.assertIn('spin_some(std::chrono::milliseconds(10))', self.main)
        self.assertIn('next_graph=now+std::chrono::milliseconds(100)', self.main)
        self.assertIn('KeepLast(8)', self.source)
        self.assertIn('beams>=24&&beams<=4096', self.source)  # Beam cap is not a sample-count cap.
        for forbidden in ('MAX_SAMPLES', 'MAX_OUTPUT', 'constexpr int SECONDS=', 'samples_max', 'stdout_bytes_max'):
            self.assertNotIn(forbidden, self.source)

    def test_signed63_counters_guard_before_output_and_subtraction(self):
        self.assertIn('COUNTER_MAX=uint64_t(INT64_MAX)', self.source)
        emit = section(self.source, '  void emit(', '// Iron\'s generic callback')
        self.assertIn('seq<COUNTER_MAX-(final?0u:1u)', emit)
        self.assertIn('samples<COUNTER_MAX', emit)
        self.assertLess(emit.index('bytes<=COUNTER_MAX-reserve'), emit.index('COUNTER_MAX-reserve-bytes'))
        self.assertLess(emit.index('NDJSON_capacity'), emit.index('std::cout<<line'))
        self.assertLess(emit.index('stdout_delivery_failed'), emit.index('++seq'))
        receive = section(self.source, '  void receive(', '  Output & output_;')
        self.assertLess(receive.index('counts[index]<COUNTER_MAX'), receive.index('output_.emit('))
        difference = section(self.source, 'int64_t checked_difference(', 'std::string hex(')
        self.assertLess(difference.index('newer>=0&&older>=0'), difference.index('return newer-older'))

    def test_counter_boundary_contract_beyond_old_finite_caps(self):
        # Check the documented integer domain and final reserve arithmetic only;
        # this does not execute or qualify C++ emission.
        cap = (1 << 63) - 1
        reserve = int(re.search(r'FINAL_RESERVE=(\d+)u', self.source).group(1))
        self.assertGreater(cap, 4096)
        self.assertGreater(cap - reserve, 32 * 1024 * 1024)
        for size in (0, 1, reserve, 2 * 256 * 1024 + reserve):
            maximum_prior = cap - reserve - size
            self.assertEqual(maximum_prior + size + reserve, cap)
            self.assertGreater(maximum_prior + 1 + size + reserve, cap)
        for newer, older in ((0, cap), (cap, 0), (cap, cap), (0, 0)):
            self.assertLessEqual(abs(newer - older), cap)
        self.assertIn('line.size()<=MAX_LINE', self.source)
        self.assertIn('MAX_LINE=2u*MAX_CDR+8192u', self.source)

    def test_two_subscriptions_no_control_or_implicit_ros_publishers(self):
        constructor = section(self.source, '  Observer(Output & output,', '  std::string fault;')
        self.assertEqual(constructor.count('std::make_shared<OriginalSubscription>'), 1)
        self.assertIn('for(bool scan:{true,false})', constructor)
        self.assertIn('qos.best_effort().durability_volatile()', constructor)
        self.assertIn('sub.use_default_callbacks=false', constructor)
        for forbidden in ('create_publisher', 'create_service', 'create_client', 'fork(', 'execve(', 'system(', 'popen(', 'socket(', 'ofstream', '/dev/', 'PWM'):
            self.assertNotIn(forbidden, self.source)
        for gate in ('enable_rosout(false)', 'enable_logger_service(false)', 'start_parameter_services(false)', 'start_parameter_event_publisher(false)', 'use_global_arguments(false)', 'RCL_SYSTEM_TIME'):
            self.assertIn(gate, self.source)

    def test_done_follows_executor_subscription_node_and_ros_cleanup(self):
        cleanup = self.main.split('  auto cleanup_error=', 1)[1]
        events = ('executor->cancel()', 'executor->remove_node(node)', 'executor.reset()', 'node->close_subscriptions()', 'node.reset()', 'rclcpp::shutdown()', 'output.emit(')
        indices = [cleanup.index(event) for event in events]
        self.assertEqual(indices, sorted(indices))
        self.assertIn('initialized&&executor_destroyed&&subscriptions_destroyed&&destroyed&&shutdown&&stop_requested&&error.empty()', cleanup)
        self.assertNotIn('initialized&&ready&&', cleanup)
        self.assertIn('error.empty()&&stop_requested?"operator_stop":"fault"', cleanup)
        for field in ('ready_observed', 'ROS_executor_destroyed', 'ROS_subscriptions_destroyed', 'ROS_node_destroyed', 'ROS_shutdown_completed', 'observed_elapsed_clock'):
            self.assertIn(field, cleanup)
        self.assertEqual(self.source.count('\\"streaming\\":true'), 3)
        self.assertIn('\\"physical_autonomy_qualified\\":false', cleanup)
        self.assertNotIn('\\"mission_admission\\":true', self.source)

    def test_cmake_only_derives_target_name(self):
        old = (FINITE.parent / 'CMakeLists.txt').read_text(encoding='utf-8')
        current = (CPP.parent / 'CMakeLists.txt').read_text(encoding='utf-8')
        self.assertEqual(current, old.replace('actual_cdr_observer', 'actual_cdr_stream'))
        self.assertIn('cxx_std_17', current)
        self.assertIn('OpenSSL::Crypto', current)


if __name__ == '__main__':
    unittest.main()
