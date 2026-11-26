# Perl modules for nfit, nfit-profile and nfit-stage-nmon that do not ship with Perl.
#   cpanm --installdeps .        (or: cpan JSON List::MoreUtils)
requires 'JSON';
requires 'List::MoreUtils';

# Faster JSON reading and writing; JSON uses it automatically when installed.
recommends 'JSON::XS';
