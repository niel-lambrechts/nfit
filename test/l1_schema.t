#!/usr/bin/perl
# Regression cover for the schema-aware L1 reader in nfit: header validation,
# manifest-declared optional columns and the L2 data identity. Extracts the
# live subroutines from nfit, so the assertions run against production source.
use strict;
use warnings;
use File::Temp qw(tempdir);

my $SRC = $ENV{NFIT_ENGINE} // '../nfit';
open my $fh, '<', $SRC or die "cannot open $SRC: $!";
my $src = do { local $/; <$fh> };
close $fh;

my $code = '';
for my $name (qw(_read_cache_manifest_meta _resolve_l1_layout _l1_data_identity))
{
    $src =~ /^(sub \Q$name\E\s*\{.*?^\})/ms or die "could not extract $name from $SRC\n";
    $code .= "$1\n\n";
}
# Globals the extracted subroutines reference, taken from the live source so
# the test follows any change to the supported set.
my %globals;
for my $g (qw(L1_SUPPORTED_SCHEMA_VERSIONS L1_REQUIRED_COLUMNS L1_COLUMN_ALIASES))
{
    $src =~ /^(my\s+[\@%]\Q$g\E\s*=.*?;)\s*$/m or die "could not extract $g\n";
    $globals{$g} = $1;
}
my $harness = "package UUT;\nuse File::Spec;\nour \$VERSION = 'test';\n"
    . "our \$CACHE_MANIFEST_FILE = '.nfit.cache.manifest';\n"
    . "our \$CACHE_DATA_FILE = '.nfit.cache.data';\n"
    . "our \$CACHE_STATES_FILE = '.nfit.cache.states';\n"
    . join("\n", map { (my $x = $_) =~ s/^my /our /; $x } values %globals) . "\n"
    . $code . "\n1;\n";
eval $harness or die "harness eval failed: $@";

my ($tests, $fails) = (0, 0);
sub ok
{
    my ($cond, $name) = @_;
    $tests++;
    if ($cond) { print "ok $tests - $name\n"; }
    else       { $fails++; print "not ok $tests - $name\n"; }
}
sub dies_like
{
    my ($sub, $re, $name) = @_;
    my $ok = !eval { $sub->(); 1 } && $@ =~ $re;
    ok($ok, $name . ($ok ? '' : " (got: " . ($@ // 'no error') . ")"));
}

my $f = '/x/.nfit.cache.data';
my $L;

# Schema 1
$L = UUT::_resolve_l1_layout("Timestamp,VMName,PhysC,RunQ\n", {}, $f);
ok($L->{legacy} == 1 && $L->{schema} == 1, 'schema 1 header takes the legacy fast path');
$L = UUT::_resolve_l1_layout("Timestamp,VM_Name,PhysC,RunQ\r\n", {}, $f);
ok($L->{legacy} == 1 && $L->{index}{VMName} == 1, 'VM_Name alias and CRLF accepted');
dies_like(sub { UUT::_resolve_l1_layout("Timestamp,VMName,PhysC,RunQ,PhysB\n", {}, $f) },
          qr/beyond schema 1/, 'extra column without a declared schema is fatal');
dies_like(sub { UUT::_resolve_l1_layout("Timestamp,VMName,RunQ,PhysC\n", {}, $f) },
          qr/must begin with/, 'misordered required columns are fatal');
dies_like(sub { UUT::_resolve_l1_layout(undef, {}, $f) }, qr/no header/, 'missing header is fatal');

# Schema 2
my $h2 = 'Timestamp,VMName,PhysC,RunQ,PhysB,FreqPct';
$L = UUT::_resolve_l1_layout("$h2\n", { data_schema_version => 2, data_columns => $h2 }, $f);
ok($L->{legacy} == 0 && $L->{index}{PhysB} == 4 && $L->{index}{FreqPct} == 5, 'schema 2 maps declared optional columns');
dies_like(sub { UUT::_resolve_l1_layout("$h2\n", { data_schema_version => 2, data_columns => 'Timestamp,VMName,PhysC,RunQ,PhysB' }, $f) },
          qr/does not match/, 'header differing from data_columns is fatal');
dies_like(sub { UUT::_resolve_l1_layout("Timestamp,VMName,PhysC,RunQ,PhysB,PhysB\n",
                { data_schema_version => 2, data_columns => 'Timestamp,VMName,PhysC,RunQ,PhysB,PhysB' }, $f) },
          qr/repeats column/, 'repeated column is fatal');
dies_like(sub { UUT::_resolve_l1_layout("$h2\n", { data_schema_version => 3, data_columns => $h2 }, $f) },
          qr/supports 1, 2/, 'unsupported schema version is fatal and names the supported set');

# Manifest parsing and data identity
my $dir = tempdir(CLEANUP => 1);
open my $m, '>', "$dir/.nfit.cache.manifest" or die;
print $m "cache_build_status: success\ndata_schema_version: 2\ndata_columns: $h2\nbuild_timestamp: 2026-11-06T00:00:00+00:00\n";
close $m;
my $meta = UUT::_read_cache_manifest_meta($dir);
ok(($meta->{data_schema_version} // '') eq '2' && ($meta->{data_columns} // '') eq $h2
   && ($meta->{build_timestamp} // '') eq '2026-11-06T00:00:00+00:00', 'manifest key/value lines parsed (value keeps its colons)');
ok(ref(UUT::_read_cache_manifest_meta("$dir/missing")) eq 'HASH', 'absent manifest yields an empty hash');

open my $d, '>', "$dir/.nfit.cache.data" or die; print $d "Timestamp,VMName,PhysC,RunQ\n"; close $d;
open my $s, '>', "$dir/.nfit.cache.states" or die; print $s "{}"; close $s;
my $id1 = UUT::_l1_data_identity($dir, {});
open $d, '>>', "$dir/.nfit.cache.data" or die; print $d "2026-11-01 00:00:00,vm,1.0,2\n"; close $d;
my $id2 = UUT::_l1_data_identity($dir, {});
ok($id1->{data} ne $id2->{data} && $id1->{states} eq $id2->{states}, 'appending data changes the data identity only');
my $id3 = UUT::_l1_data_identity($dir, { data_revision => 'g7', states_revision => 's3', data_schema_version => 2 });
ok($id3->{data} eq 'rev:g7' && $id3->{states} eq 'rev:s3' && $id3->{schema} == 2, 'manifest revisions take precedence over file stat');

print "\n$tests tests, $fails failures\n";
exit($fails ? 1 : 0);
